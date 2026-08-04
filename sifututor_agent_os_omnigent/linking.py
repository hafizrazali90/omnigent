"""Link the current Omnigent chat to one exact Agent OS Session Map."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .continuity import validate_session_map_pointer

_SESSION_ID_ENV = ("OMNIGENT_SESSION_ID", "_OMNIGENT_SESSION_ID")
_SERVER_URL_ENV = (
    "OMNIGENT_SERVER_URL",
    "RUNNER_SERVER_URL",
    "_OMNIGENT_SERVER_URL",
    "OMNIGENT_POLICY_URL",
)
_WORKSPACE_ROOT_ENV = "AGENT_OS_WORKSPACE_ROOT"


def _first_environment_value(names: Sequence[str]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _validated_server_url(server_url: str) -> str:
    value = server_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Omnigent server URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Omnigent server URL must not contain credentials")
    return value


def link_current_session(
    *,
    workspace_root: Path,
    session_map: str,
    session_id: str,
    server_url: str,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Validate *session_map*, then attach it to the current Omnigent chat."""
    current_session_id = session_id.strip()
    if not current_session_id:
        raise ValueError("current Omnigent session identity is unavailable")
    pointer = validate_session_map_pointer(workspace_root, session_map)
    base_url = _validated_server_url(server_url)
    with httpx.Client(base_url=base_url, transport=transport, timeout=10.0) as client:
        response = client.patch(
            f"/v1/sessions/{quote(current_session_id, safe='')}",
            json={"labels": {"agent_os.session_map": pointer}},
        )
    response.raise_for_status()
    payload = response.json()
    labels = payload.get("labels") if isinstance(payload, dict) else None
    if not isinstance(labels, dict) or labels.get("agent_os.session_map") != pointer:
        raise RuntimeError("Omnigent did not confirm the Agent OS Session Map link")
    return pointer


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by the Agent OS task router inside a native chat."""
    parser = argparse.ArgumentParser(
        description="Link this Omnigent chat to one exact Agent OS Session Map."
    )
    parser.add_argument("session_map", help="Workspace-relative Session Map path")
    parser.add_argument(
        "--workspace-root",
        default=os.environ.get(_WORKSPACE_ROOT_ENV, ""),
        help=f"Agent OS root (or ${_WORKSPACE_ROOT_ENV})",
    )
    args = parser.parse_args(argv)

    workspace_root = str(args.workspace_root).strip()
    if not workspace_root:
        parser.error(f"--workspace-root or ${_WORKSPACE_ROOT_ENV} is required")
    session_id = _first_environment_value(_SESSION_ID_ENV)
    server_url = _first_environment_value(_SERVER_URL_ENV)
    if not session_id:
        parser.error("this command must run inside an Omnigent task session")
    if not server_url:
        parser.error("the Omnigent server address is unavailable")

    pointer = link_current_session(
        workspace_root=Path(workspace_root),
        session_map=args.session_map,
        session_id=session_id,
        server_url=server_url,
    )
    print(json.dumps({"linked": True, "session_map": pointer}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
