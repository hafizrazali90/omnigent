"""Permanent browser journey for correctable Agent OS task understanding."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect


def test_focused_task_shows_and_corrects_its_agent_os_understanding(
    page: Page,
    seeded_session: tuple[str, str],
    tmp_path: Path,
) -> None:
    """The route summary stays inside the task and corrections update that task only."""
    base_url, session_id = seeded_session
    response = httpx.patch(
        f"{base_url}/v1/sessions/{session_id}",
        json={
            "title": "Review the Ripple dashboard",
            "labels": {
                "agent_os.project": "ripple-suite",
                "agent_os.workflow": "review",
                "agent_os.finish_line": "PR opened",
                "agent_os.finish_line_source": "orchestrator",
                "agent_os.proven_state": "Pushed branch; no PR",
                "agent_os.proven_state_evidence": "Remote SHA abc123 matches local",
                "agent_os.proven_state_source": "orchestrator",
                "agent_os.route_status": "understood",
                "agent_os.route_question": "",
                "agent_os.route_source": "orchestrator",
            },
        },
        timeout=10.0,
    )
    response.raise_for_status()

    page.goto(f"{base_url}/c/{session_id}")
    expand_workspace = page.get_by_role("button", name="Expand right panel")
    if expand_workspace.is_visible():
        expand_workspace.click()
    workspace = page.get_by_label("Workspace")
    expect(workspace).to_be_visible(timeout=30_000)
    workspace.get_by_role("tab", name=re.compile(r"Agents \d")).click()

    understanding = page.get_by_test_id("agent-os-task-understanding")
    expect(understanding).to_be_visible()
    expect(understanding.get_by_text("ripple-suite", exact=True)).to_be_visible()
    expect(understanding.get_by_text("review", exact=True)).to_be_visible()
    expect(understanding.get_by_text("PR opened", exact=True)).to_be_visible()
    expect(understanding.get_by_text("Pushed branch; no PR", exact=True)).to_be_visible()
    expect(
        understanding.get_by_text("Remote SHA abc123 matches local", exact=True)
    ).to_be_visible()

    understanding.get_by_role("button", name="Correct understanding").click()
    understanding.get_by_label("Project").fill("sifu-tutor")
    understanding.get_by_label("Workflow").fill("bugfix")
    understanding.get_by_label("Finish line").fill("Production monitored")
    understanding.get_by_role("button", name="Save correction").click()

    expect(understanding.get_by_text("sifu-tutor", exact=True)).to_be_visible()
    expect(understanding.get_by_text("bugfix", exact=True)).to_be_visible()
    expect(understanding.get_by_text("Production monitored", exact=True)).to_be_visible()
    expect(understanding.get_by_text("Corrected by you", exact=True)).to_be_visible()
    page.screenshot(path=str(tmp_path / "agent-os-task-understanding.png"), full_page=True)

    saved = httpx.get(f"{base_url}/v1/sessions/{session_id}", timeout=10.0)
    saved.raise_for_status()
    labels = saved.json()["labels"]
    assert labels["agent_os.project"] == "sifu-tutor"
    assert labels["agent_os.workflow"] == "bugfix"
    assert labels["agent_os.finish_line"] == "Production monitored"
    assert labels["agent_os.finish_line_source"] == "user-corrected"
    assert labels["agent_os.route_status"] == "confirmed"
    assert labels["agent_os.route_source"] == "user-corrected"
