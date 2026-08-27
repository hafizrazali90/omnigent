"""Permanent browser journey for focused-task Agent OS continuity."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
from playwright.sync_api import Page, Route, expect

_SESSION_MAP = ".agent-os/session-maps/native-agent-os.md"


def test_focused_task_reads_only_its_linked_session_map(
    page: Page,
    seeded_session: tuple[str, str],
) -> None:
    """The Worker Sidebar uses the task's exact map pointer and shows its live summary."""
    base_url, session_id = seeded_session
    response = httpx.patch(
        f"{base_url}/v1/sessions/{session_id}",
        json={
            "title": "Build Agent OS continuity",
            "labels": {"agent_os.session_map": _SESSION_MAP},
        },
        timeout=10.0,
    )
    response.raise_for_status()

    continuity_reads = 0

    def _continuity(route: Route) -> None:
        nonlocal continuity_reads
        continuity_reads += 1
        query = parse_qs(urlparse(route.request.url).query)
        assert query == {"session_map": [_SESSION_MAP]}
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {
                    "object": "agent_os.continuity",
                    "goal": "One place to run and remember development work.",
                    "now": (
                        "The continuity bridge is connected to this task."
                        if continuity_reads == 1
                        else "The same task picked up its refreshed Session Map."
                    ),
                    "next": "Verify the original records remain unchanged.",
                    "decision_needed": "No.",
                    "session_map": _SESSION_MAP,
                    "source_updated_at": (
                        "2026-08-04T05:30:00Z" if continuity_reads == 1 else "2026-08-04T05:31:00Z"
                    ),
                    "follow_up_count": 1,
                    "follow_ups": [
                        {
                            "id": "AO-LATER-001",
                            "title": "Telegram access",
                            "status": "paused",
                            "project": "cross-project",
                            "next_action": "Resume after the desktop workflow is stable.",
                            "source": "docs/agent-playbooks/mission-ledger/cross-project.md",
                        }
                    ],
                }
            ),
        )

    page.route("**/v1/agent-os/continuity?*", _continuity)
    page.goto(f"{base_url}/c/{session_id}")

    expand_workspace = page.get_by_role("button", name="Expand right panel")
    if expand_workspace.is_visible():
        expand_workspace.click()
    workspace = page.get_by_label("Workspace")
    expect(workspace).to_be_visible(timeout=30_000)
    workspace.get_by_role("tab", name=re.compile(r"Session tree \d")).click()

    task_brief = page.get_by_test_id("agent-os-task-brief")
    expect(task_brief).to_be_visible()
    task_brief.get_by_text("Continuity details", exact=True).click()
    continuity = page.get_by_test_id("agent-os-continuity")
    expect(continuity).to_be_visible()
    expect(
        continuity.get_by_text("One place to run and remember development work.", exact=True)
    ).to_be_visible()
    expect(
        continuity.get_by_text(
            "The continuity bridge is connected to this task.",
            exact=True,
        )
    ).to_be_visible()
    expect(continuity.get_by_text("Source updated", exact=False)).to_be_visible()
    expect(
        continuity.get_by_text(
            "The same task picked up its refreshed Session Map.",
            exact=True,
        )
    ).to_be_visible(timeout=20_000)
    expect(continuity.get_by_text("1 remembered follow-up", exact=True)).to_be_visible()
    continuity.get_by_text("1 remembered follow-up", exact=True).click()
    expect(continuity.get_by_text("Telegram access", exact=True)).to_be_visible()
    expect(
        continuity.get_by_text(
            "Resume after the desktop workflow is stable.",
            exact=True,
        )
    ).to_be_visible()
