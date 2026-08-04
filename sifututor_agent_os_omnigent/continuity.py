"""Read-only Session Map and Mission Ledger bridge for Omnigent."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Query, Response

_SESSION_MAP_GLOB = ".agent-os/session-maps/*.md"
_MISSION_LEDGER_GLOB = "docs/agent-playbooks/mission-ledger/*.md"
_UNRESOLVED_STATUSES = frozenset({"active", "paused", "triaged", "captured"})
_STATUS_ORDER = {"paused": 0, "active": 1, "triaged": 2, "captured": 3}


def _single_line(value: str) -> str:
    return " ".join(part.strip() for part in value.splitlines() if part.strip())


def _field(block: str, label: str) -> str | None:
    pattern = re.compile(
        rf"^- \*\*{re.escape(label)}:\*\*\s*(.*(?:\n  (?!- \*\*).*)*)",
        re.MULTILINE,
    )
    match = pattern.search(block)
    return _single_line(match.group(1)) if match else None


def _human_snapshot(markdown: str) -> str:
    match = re.search(
        r"^## Human Snapshot\s*$\n(?P<body>.*?)(?=^## |\Z)",
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    return match.group("body") if match else ""


def _safe_pattern(pattern: object, default: str) -> str | None:
    value = pattern if isinstance(pattern, str) and pattern.strip() else default
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return value


def _matching_files(root: Path, pattern: str) -> list[Path]:
    return [
        path
        for path in root.glob(pattern)
        if path.is_file() and path.resolve().is_relative_to(root)
    ]


def _selected_session_map(
    root: Path, selected: str | None, pattern: str
) -> tuple[Path, str] | None:
    if selected is None:
        return None
    candidate = Path(selected)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("session_map must stay inside the configured Session Map directory")
    path = (root / candidate).resolve()
    allowed = {entry.resolve() for entry in _matching_files(root, pattern)}
    if path not in allowed:
        raise ValueError("session_map is not an available Agent OS Session Map")
    return path, path.read_text(encoding="utf-8")


def validate_session_map_pointer(
    workspace_root: Path,
    session_map: str,
    *,
    session_map_glob: str = _SESSION_MAP_GLOB,
) -> str:
    """Return a safe source-relative pointer to an existing Session Map."""
    root = workspace_root.expanduser().resolve()
    pattern = _safe_pattern(session_map_glob, _SESSION_MAP_GLOB)
    if pattern is None:
        raise ValueError("Session Map pattern must stay inside the workspace root")
    selected = _selected_session_map(root, session_map, pattern)
    if selected is None:
        raise ValueError("session_map must name an exact Agent OS Session Map")
    return str(selected[0].relative_to(root))


def _mission_follow_ups(root: Path, files: list[Path]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for path in files:
        markdown = path.read_text(encoding="utf-8")
        headings = list(
            re.finditer(
                r"^### (?P<id>[A-Z0-9][A-Z0-9.-]*) — (?P<title>.+?)\s*$",
                markdown,
                re.MULTILINE,
            )
        )
        for index, heading in enumerate(headings):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
            block = markdown[heading.end() : end]
            status = (_field(block, "Status") or "").lower()
            if status not in _UNRESOLVED_STATUSES:
                continue
            items.append(
                {
                    "id": heading.group("id"),
                    "title": heading.group("title").strip(),
                    "status": status,
                    "project": _field(block, "Project") or "unknown",
                    "next_action": _field(block, "Next action") or "Review this follow-up.",
                    "source": str(path.relative_to(root)),
                }
            )
    return sorted(
        items,
        key=lambda item: (_STATUS_ORDER.get(item["status"], 99), item["id"]),
    )


def _source_updated_at(current: tuple[Path, str] | None, ledgers: list[Path]) -> str | None:
    paths = [*([] if current is None else [current[0]]), *ledgers]
    if not paths:
        return None
    modified_at = max(path.stat().st_mtime for path in paths)
    return (
        datetime.fromtimestamp(modified_at, tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def build_continuity_snapshot(
    workspace_root: Path,
    *,
    session_map: str | None = None,
    session_map_glob: str = _SESSION_MAP_GLOB,
    mission_ledger_glob: str = _MISSION_LEDGER_GLOB,
) -> dict[str, object]:
    """Read current Agent OS continuity from its existing Markdown owners."""
    root = workspace_root.expanduser().resolve()
    current = _selected_session_map(root, session_map, session_map_glob)
    markdown = current[1] if current is not None else ""
    snapshot = _human_snapshot(markdown)
    ledger_files = sorted(_matching_files(root, mission_ledger_glob))
    follow_ups = _mission_follow_ups(root, ledger_files)
    return {
        "object": "agent_os.continuity",
        "goal": _field(snapshot, "Started because"),
        "now": _field(snapshot, "Right now"),
        "next": _field(snapshot, "Next recommended move"),
        "decision_needed": _field(snapshot, "Decision needed from Hafiz"),
        "session_map": str(current[0].relative_to(root)) if current is not None else None,
        "source_updated_at": _source_updated_at(current, ledger_files),
        "follow_up_count": len(follow_ups),
        "follow_ups": follow_ups[:12],
    }


def create_extension_routers(
    server_config: Mapping[str, object],
) -> list[tuple[APIRouter, str, list[str]]]:
    """Create the continuity API only when an explicit safe root is configured."""
    raw_config = server_config.get("agent_os_continuity")
    if not isinstance(raw_config, dict):
        return []
    config = cast(dict[str, object], raw_config)
    root_value = config.get("workspace_root")
    if not isinstance(root_value, str) or not root_value.strip():
        return []
    if not Path(root_value).is_absolute():
        return []
    session_pattern = _safe_pattern(config.get("session_map_glob"), _SESSION_MAP_GLOB)
    ledger_pattern = _safe_pattern(config.get("mission_ledger_glob"), _MISSION_LEDGER_GLOB)
    if session_pattern is None or ledger_pattern is None:
        return []

    workspace_root = Path(root_value)
    router = APIRouter()

    @router.get("/continuity")
    async def continuity(
        response: Response,
        session_map: str | None = Query(default=None),
    ) -> dict[str, object]:
        response.headers["Cache-Control"] = "no-store"
        try:
            return build_continuity_snapshot(
                workspace_root,
                session_map=session_map,
                session_map_glob=session_pattern,
                mission_ledger_glob=ledger_pattern,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return [(router, "/v1/agent-os", ["agent-os"])]


__all__ = [
    "build_continuity_snapshot",
    "create_extension_routers",
    "validate_session_map_pointer",
]
