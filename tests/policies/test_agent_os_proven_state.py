"""Exact-current-session tests for the Agent OS proven-state helper."""

from __future__ import annotations

import json

import httpx
import pytest

from sifututor_agent_os_omnigent.state import main, set_current_session_proven_state


def _transport(expected_session_id: str, expected_labels: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == f"/v1/sessions/{expected_session_id}"
        assert json.loads(request.content) == {"labels": expected_labels}
        return httpx.Response(200, json={"labels": expected_labels})

    return httpx.MockTransport(handler)


def test_proven_state_helper_updates_only_the_current_session() -> None:
    labels = {
        "agent_os.proven_state": "Pushed branch; no PR",
        "agent_os.proven_state_evidence": "Remote SHA abc123 matches local",
        "agent_os.proven_state_source": "orchestrator",
    }

    result = set_current_session_proven_state(
        state="Pushed branch; no PR",
        evidence="Remote SHA abc123 matches local",
        session_id="conv_current",
        server_url="http://127.0.0.1:17677",
        transport=_transport("conv_current", labels),
    )

    assert result == labels


@pytest.mark.parametrize(
    ("state", "evidence"),
    [
        ("", "Fresh Git status"),
        ("Pushed", ""),
        ("x" * 181, "Fresh Git status"),
        ("Pushed", "x" * 241),
    ],
)
def test_proven_state_helper_rejects_missing_or_unbounded_values(
    state: str, evidence: str
) -> None:
    with pytest.raises(ValueError):
        set_current_session_proven_state(
            state=state,
            evidence=evidence,
            session_id="conv_current",
            server_url="http://127.0.0.1:17677",
        )


def test_proven_state_cli_uses_native_current_session_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OMNIGENT_SESSION_ID", "conv_current")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://127.0.0.1:17677")
    captured: dict[str, object] = {}

    def fake_set(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {
            "agent_os.proven_state": "Pushed branch; no PR",
            "agent_os.proven_state_evidence": "Remote SHA abc123 matches local",
        }

    monkeypatch.setattr(
        "sifututor_agent_os_omnigent.state.set_current_session_proven_state",
        fake_set,
    )

    assert (
        main(
            [
                "--state",
                "Pushed branch; no PR",
                "--evidence",
                "Remote SHA abc123 matches local",
            ]
        )
        == 0
    )
    assert captured["session_id"] == "conv_current"
    assert captured["server_url"] == "http://127.0.0.1:17677"
    assert captured["evidence"] == "Remote SHA abc123 matches local"
    assert json.loads(capsys.readouterr().out)["state"] == "Pushed branch; no PR"
