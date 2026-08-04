"""Exact-current-session tests for the Agent OS task-understanding helper."""

from __future__ import annotations

import json

import httpx
import pytest

from sifututor_agent_os_omnigent.understanding import (
    main,
    set_current_session_understanding,
)


def _transport(expected_session_id: str, expected_labels: dict[str, str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == f"/v1/sessions/{expected_session_id}"
        assert json.loads(request.content) == {"labels": expected_labels}
        return httpx.Response(200, json={"labels": expected_labels})

    return httpx.MockTransport(handler)


def test_understanding_helper_sets_an_unambiguous_route_on_only_the_current_session() -> None:
    labels = {
        "agent_os.project": "sifututor-agent-os",
        "agent_os.workflow": "feature",
        "agent_os.route_status": "understood",
        "agent_os.route_question": "",
        "agent_os.route_source": "orchestrator",
    }

    result = set_current_session_understanding(
        project="sifututor-agent-os",
        workflow="feature",
        question="",
        session_id="conv_current",
        server_url="http://127.0.0.1:17677",
        transport=_transport("conv_current", labels),
    )

    assert result == labels


def test_understanding_helper_keeps_material_ambiguity_visible() -> None:
    labels = {
        "agent_os.project": "umbrella",
        "agent_os.workflow": "triage",
        "agent_os.route_status": "needs-clarification",
        "agent_os.route_question": "Which product should this change?",
        "agent_os.route_source": "orchestrator",
    }

    result = set_current_session_understanding(
        project="umbrella",
        workflow="triage",
        question="Which product should this change?",
        session_id="conv_current",
        server_url="http://127.0.0.1:17677",
        transport=_transport("conv_current", labels),
    )

    assert result["agent_os.route_status"] == "needs-clarification"


@pytest.mark.parametrize(
    ("project", "workflow"),
    [
        ("", "feature"),
        ("sifututor", ""),
        ("x" * 121, "feature"),
        ("sifututor", "x" * 121),
    ],
)
def test_understanding_helper_rejects_missing_or_unbounded_route_values(
    project: str,
    workflow: str,
) -> None:
    with pytest.raises(ValueError):
        set_current_session_understanding(
            project=project,
            workflow=workflow,
            question="",
            session_id="conv_current",
            server_url="http://127.0.0.1:17677",
        )


def test_understanding_cli_uses_native_current_session_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("OMNIGENT_SESSION_ID", "conv_current")
    monkeypatch.setenv("OMNIGENT_SERVER_URL", "http://127.0.0.1:17677")
    captured: dict[str, object] = {}

    def fake_set(**kwargs: object) -> dict[str, str]:
        captured.update(kwargs)
        return {
            "agent_os.project": "ripple-suite",
            "agent_os.workflow": "review",
            "agent_os.route_status": "understood",
        }

    monkeypatch.setattr(
        "sifututor_agent_os_omnigent.understanding.set_current_session_understanding",
        fake_set,
    )

    assert main(["--project", "ripple-suite", "--workflow", "review"]) == 0
    assert captured["session_id"] == "conv_current"
    assert captured["server_url"] == "http://127.0.0.1:17677"
    assert json.loads(capsys.readouterr().out)["project"] == "ripple-suite"
