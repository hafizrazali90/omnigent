"""UI journey: compare real sessions in the Control Room and enter one.

The sessions are created through Omnigent's real API and rendered through the
real ``GET /v1/sessions`` path. The test then follows the Control Room link into
the unchanged conversation workspace, proving that the overview is an additive
entry point rather than a replacement chat implementation.
"""

from __future__ import annotations

import httpx
from playwright.sync_api import Page, expect


def _title_session(base_url: str, session_id: str, title: str) -> None:
    response = httpx.patch(
        f"{base_url}/v1/sessions/{session_id}",
        json={"title": title},
        timeout=10.0,
    )
    response.raise_for_status()


def test_control_room_renders_real_sessions_and_opens_original_workspace(
    page: Page,
    seeded_session_pair: tuple[str, str, str],
) -> None:
    base_url, session_a, session_b = seeded_session_pair
    _title_session(base_url, session_a, "Review payment safeguards")
    _title_session(base_url, session_b, "Prepare CRM release")

    page.goto(f"{base_url}/control-room")

    expect(page.get_by_role("heading", name="Control Room")).to_be_visible(timeout=30_000)
    lanes = page.get_by_test_id("control-room-lane")
    expect(lanes).to_have_count(2, timeout=30_000)
    expect(lanes.filter(has_text="Review payment safeguards")).to_be_visible()
    crm_lane = lanes.filter(has_text="Prepare CRM release")
    expect(crm_lane).to_be_visible()
    crm_lane.get_by_role("link", name="Open task").click()
    expect(page).to_have_url(f"{base_url}/c/{session_b}")
    expect(page.get_by_label("Message the agent")).to_be_visible(timeout=30_000)
