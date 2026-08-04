"""Permanent browser journey across the native Agent OS workspace surfaces."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect


def _prepare_task(
    base_url: str,
    session_id: str,
    *,
    title: str,
    project: str,
    finish_line: str,
    proven_state: str,
) -> None:
    response = httpx.patch(
        f"{base_url}/v1/sessions/{session_id}",
        json={
            "title": title,
            "labels": {
                "agent_os.project": project,
                "agent_os.workflow": "review",
                "agent_os.finish_line": finish_line,
                "agent_os.finish_line_source": "orchestrator",
                "agent_os.proven_state": proven_state,
                "agent_os.proven_state_evidence": "Verified from the current task state",
                "agent_os.proven_state_source": "orchestrator",
                "agent_os.route_status": "confirmed",
                "agent_os.route_question": "",
                "agent_os.route_source": "orchestrator",
            },
        },
        timeout=10.0,
    )
    response.raise_for_status()


def test_agent_os_workspace_preserves_task_truth_across_all_four_surfaces(
    page: Page,
    seeded_session_pair: tuple[str, str, str],
    tmp_path: Path,
) -> None:
    """Control Room → focused task/sidebar → Split Focus → Needs You."""
    base_url, session_a, session_b = seeded_session_pair
    _prepare_task(
        base_url,
        session_a,
        title="Review payment safeguards",
        project="sifu-tutor",
        finish_line="PR opened",
        proven_state="Focused tests passed; branch is local",
    )
    _prepare_task(
        base_url,
        session_b,
        title="Prepare CRM release",
        project="ripple-suite",
        finish_line="Staging verified",
        proven_state="Implementation complete; staging not run",
    )
    page.set_viewport_size({"width": 2200, "height": 1200})

    page.goto(f"{base_url}/control-room")
    expect(page.get_by_role("heading", name="Control Room")).to_be_visible(timeout=30_000)
    expect(page.get_by_role("searchbox", name="Search tasks")).to_be_visible()
    expect(page.get_by_role("combobox", name="Visible columns")).to_have_value("4")
    payment_lane = page.get_by_test_id("control-room-lane").filter(
        has_text="Review payment safeguards"
    )
    expect(payment_lane.get_by_text("PR opened", exact=True)).to_be_visible()
    expect(
        payment_lane.get_by_text("Focused tests passed; branch is local", exact=True)
    ).to_be_visible()
    page.screenshot(path=str(tmp_path / "agent-os-control-room-assembly.png"), full_page=True)

    payment_lane.get_by_role("link", name="Open task").click()
    expect(page).to_have_url(f"{base_url}/c/{session_a}")
    expand_workspace = page.get_by_role("button", name="Expand right panel")
    if expand_workspace.is_visible():
        expand_workspace.click()
    workspace = page.get_by_label("Workspace")
    expect(workspace).to_be_visible(timeout=30_000)
    workspace.get_by_role("tab", name=re.compile(r"Agents \d")).click()
    task_brief = page.get_by_test_id("agent-os-task-brief")
    expect(task_brief).to_be_visible()
    expect(task_brief.get_by_text("Task brief", exact=True)).to_be_visible()
    expect(task_brief.get_by_text("PR opened", exact=True)).to_be_visible()
    page.screenshot(path=str(tmp_path / "agent-os-worker-task-brief.png"), full_page=True)

    page.goto(f"{base_url}/split-focus?session={session_a}&session={session_b}")
    expect(page.get_by_role("heading", name="Split Focus")).to_be_visible(timeout=30_000)
    expect(page.get_by_role("searchbox", name="Search Split Focus tasks")).to_be_visible()
    expect(page.get_by_test_id("split-focus-pane")).to_have_count(2)
    expect(
        page.frame_locator(
            'iframe[title="Task workspace: Review payment safeguards"]'
        ).get_by_label("Message the agent")
    ).to_be_visible(timeout=30_000)
    expect(
        page.frame_locator('iframe[title="Task workspace: Prepare CRM release"]').get_by_label(
            "Message the agent"
        )
    ).to_be_visible(timeout=30_000)
    page.screenshot(path=str(tmp_path / "agent-os-split-focus-assembly.png"), full_page=True)

    page.goto(f"{base_url}/needs-you")
    expect(page.get_by_role("heading", name="Needs You")).to_be_visible(timeout=30_000)
