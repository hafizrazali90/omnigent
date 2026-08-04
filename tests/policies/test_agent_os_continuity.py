"""Read-only Agent OS continuity adapter tests."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sifututor_agent_os_omnigent.continuity import (
    build_continuity_snapshot,
    create_extension_routers,
)


def _write_sources(root: Path) -> None:
    session_maps = root / ".agent-os" / "session-maps"
    session_maps.mkdir(parents=True)
    (session_maps / "older.md").write_text(
        """# Session Map: Old

## Human Snapshot

- **Started because:** Old goal.
- **Right now:** Old state.
- **Next recommended move:** Old next.
- **Decision needed from Hafiz:** No.
"""
    )
    current = session_maps / "current.md"
    current.write_text(
        """# Session Map: Native Agent OS

## Human Snapshot

- **Started because:** Build one place to run and remember development work.
- **Right now:** The continuity bridge is being built.
- **What changed so far:** The core workspaces already exist.
- **Next recommended move:** Prove the bridge against the real source files.
- **Decision needed from Hafiz:** No decision is needed.

## Progress Board

| Item | Status |
| --- | --- |
| Continuity | Building |
"""
    )
    current.touch()

    ledgers = root / "docs" / "agent-playbooks" / "mission-ledger"
    ledgers.mkdir(parents=True)
    (ledgers / "cross-project.md").write_text(
        """# Cross-project Mission Ledger

### AO-RUNTIME-001 — Native Agent OS workspace

- **Project:** cross-project
- **Status:** active
- **Type:** mission
- **Parent:** none
- **End goal:** One native work environment.
- **Why it matters:** Work should not be forgotten.
- **Source:** Hafiz
- **Next action:** Continue the continuity bridge.
- **Promote to:** GitHub issue
- **Links:** none

### AO-LATER-001 — Telegram access

- **Project:** cross-project
- **Status:** paused
- **Type:** task
- **Parent:** AO-RUNTIME-001
- **End goal:** Safe phone access.
- **Why it matters:** Hafiz can respond away from the Mac.
- **Source:** Hafiz
- **Next action:** Resume after the desktop workflow is stable.
- **Promote to:** none yet
- **Links:** none

### AO-DONE-001 — Completed research

- **Project:** cross-project
- **Status:** done
- **Type:** research
- **Parent:** AO-RUNTIME-001
- **End goal:** Compare foundations.
- **Why it matters:** Choose deliberately.
- **Source:** Hafiz
- **Next action:** None.
- **Promote to:** none yet
- **Links:** none
"""
    )


def test_build_continuity_snapshot_reads_sources_without_copying_done_work(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    session_map = tmp_path / ".agent-os" / "session-maps" / "current.md"
    ledger = tmp_path / "docs" / "agent-playbooks" / "mission-ledger" / "cross-project.md"
    before = (session_map.read_bytes(), ledger.read_bytes())

    snapshot = build_continuity_snapshot(
        tmp_path,
        session_map=".agent-os/session-maps/current.md",
    )

    assert snapshot["goal"] == "Build one place to run and remember development work."
    assert snapshot["now"] == "The continuity bridge is being built."
    assert snapshot["next"] == "Prove the bridge against the real source files."
    assert snapshot["decision_needed"] == "No decision is needed."
    assert snapshot["session_map"] == ".agent-os/session-maps/current.md"
    assert snapshot["follow_up_count"] == 2
    assert [item["id"] for item in snapshot["follow_ups"]] == [
        "AO-LATER-001",
        "AO-RUNTIME-001",
    ]
    assert snapshot["follow_ups"][0]["next_action"] == (
        "Resume after the desktop workflow is stable."
    )
    assert (session_map.read_bytes(), ledger.read_bytes()) == before


def test_create_extension_routers_is_opt_in_and_rejects_outside_root(tmp_path: Path) -> None:
    assert create_extension_routers({}) == []
    assert (
        create_extension_routers({"agent_os_continuity": {"workspace_root": "relative/workspace"}})
        == []
    )
    assert (
        create_extension_routers(
            {
                "agent_os_continuity": {
                    "workspace_root": str(tmp_path),
                    "session_map_glob": "../*.md",
                }
            }
        )
        == []
    )


def test_build_continuity_snapshot_never_guesses_between_parallel_sessions(
    tmp_path: Path,
) -> None:
    _write_sources(tmp_path)

    snapshot = build_continuity_snapshot(tmp_path)

    assert snapshot["goal"] is None
    assert snapshot["now"] is None
    assert snapshot["next"] is None
    assert snapshot["session_map"] is None


def test_continuity_route_returns_live_read_only_snapshot(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    entries = create_extension_routers({"agent_os_continuity": {"workspace_root": str(tmp_path)}})
    assert len(entries) == 1

    app = FastAPI()
    router, prefix, tags = entries[0]
    app.include_router(router, prefix=prefix, tags=tags)
    client = TestClient(app)

    response = client.get(
        "/v1/agent-os/continuity",
        params={"session_map": ".agent-os/session-maps/current.md"},
    )

    assert response.status_code == 200
    assert response.json()["object"] == "agent_os.continuity"
    assert response.json()["now"] == "The continuity bridge is being built."
    assert response.headers["cache-control"] == "no-store"


def test_continuity_route_rejects_a_map_outside_the_owned_directory(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    entries = create_extension_routers({"agent_os_continuity": {"workspace_root": str(tmp_path)}})
    app = FastAPI()
    router, prefix, tags = entries[0]
    app.include_router(router, prefix=prefix, tags=tags)

    response = TestClient(app).get(
        "/v1/agent-os/continuity",
        params={"session_map": "../private.md"},
    )

    assert response.status_code == 400
