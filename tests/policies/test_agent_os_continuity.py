"""Read-only Agent OS continuity adapter tests."""

import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sifututor_agent_os_omnigent import linking
from sifututor_agent_os_omnigent.continuity import (
    build_continuity_snapshot,
    create_extension_routers,
)
from sifututor_agent_os_omnigent.linking import link_current_session


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
    assert isinstance(snapshot["source_updated_at"], str)
    assert (session_map.read_bytes(), ledger.read_bytes()) == before


def test_continuity_freshness_tracks_the_exact_map_and_ledger_sources(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    session_map = tmp_path / ".agent-os" / "session-maps" / "current.md"
    ledger = tmp_path / "docs" / "agent-playbooks" / "mission-ledger" / "cross-project.md"
    os.utime(session_map, (1_700_000_000, 1_700_000_000))
    os.utime(ledger, (1_710_000_000, 1_710_000_000))

    snapshot = build_continuity_snapshot(
        tmp_path,
        session_map=".agent-os/session-maps/current.md",
    )

    assert snapshot["source_updated_at"] == "2024-03-09T16:00:00Z"


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


def test_link_current_session_validates_then_updates_only_the_current_chat(
    tmp_path: Path,
) -> None:
    _write_sources(tmp_path)
    session_map = tmp_path / ".agent-os" / "session-maps" / "current.md"
    ledger = tmp_path / "docs" / "agent-playbooks" / "mission-ledger" / "cross-project.md"
    before = (session_map.read_bytes(), ledger.read_bytes())
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "PATCH"
        assert request.url.path == "/v1/sessions/conv_current"
        assert request.content == (
            b'{"labels":{"agent_os.session_map":".agent-os/session-maps/current.md"}}'
        )
        return httpx.Response(
            200,
            json={
                "id": "conv_current",
                "labels": {
                    "agent_os.session_map": ".agent-os/session-maps/current.md",
                },
            },
        )

    result = link_current_session(
        workspace_root=tmp_path,
        session_map=".agent-os/session-maps/current.md",
        session_id="conv_current",
        server_url="http://omnigent.test",
        transport=httpx.MockTransport(handler),
    )

    assert result == ".agent-os/session-maps/current.md"
    assert len(requests) == 1
    assert (session_map.read_bytes(), ledger.read_bytes()) == before


@pytest.mark.parametrize(
    "session_map",
    [
        "../private.md",
        "/tmp/private.md",
        ".agent-os/session-maps/missing.md",
    ],
)
def test_link_current_session_fails_closed_before_any_metadata_write(
    tmp_path: Path,
    session_map: str,
) -> None:
    _write_sources(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid Session Map must not reach Omnigent")

    with pytest.raises(ValueError, match=r"Session Map|session_map"):
        link_current_session(
            workspace_root=tmp_path,
            session_map=session_map,
            session_id="conv_current",
            server_url="http://omnigent.test",
            transport=httpx.MockTransport(handler),
        )


def test_link_current_session_requires_omnigent_owned_identity(
    tmp_path: Path,
) -> None:
    _write_sources(tmp_path)

    with pytest.raises(ValueError, match="current Omnigent session"):
        link_current_session(
            workspace_root=tmp_path,
            session_map=".agent-os/session-maps/current.md",
            session_id="",
            server_url="http://omnigent.test",
        )


def test_link_cli_uses_omnigent_owned_environment_coordinates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_sources(tmp_path)
    captured: dict[str, object] = {}

    def fake_link(**kwargs: object) -> str:
        captured.update(kwargs)
        return ".agent-os/session-maps/current.md"

    monkeypatch.setenv("OMNIGENT_SESSION_ID", "conv_current")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://omnigent.test")
    monkeypatch.setattr(linking, "link_current_session", fake_link)

    result = linking.main(
        [
            ".agent-os/session-maps/current.md",
            "--workspace-root",
            str(tmp_path),
        ]
    )

    assert result == 0
    assert captured == {
        "workspace_root": tmp_path,
        "session_map": ".agent-os/session-maps/current.md",
        "session_id": "conv_current",
        "server_url": "http://omnigent.test",
    }
    assert json.loads(capsys.readouterr().out) == {
        "linked": True,
        "session_map": ".agent-os/session-maps/current.md",
    }
