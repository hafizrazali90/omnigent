"""UI journey: reply across real Control Room sessions and enter one.

The sessions are created through Omnigent's real API and rendered through the
real ``GET /v1/sessions`` path. The test sends a different reply from each lane,
proves the server stored each reply under only its intended session, then follows
the Control Room link into the unchanged conversation workspace.
"""

from __future__ import annotations

import re

import httpx
from playwright.sync_api import Page, expect


def _title_session(base_url: str, session_id: str, title: str) -> None:
    response = httpx.patch(
        f"{base_url}/v1/sessions/{session_id}",
        json={"title": title},
        timeout=10.0,
    )
    response.raise_for_status()


def _user_message_texts(base_url: str, session_id: str) -> list[str]:
    response = httpx.get(
        f"{base_url}/v1/sessions/{session_id}/items",
        params={"limit": 20, "order": "desc"},
        timeout=10.0,
    )
    response.raise_for_status()
    texts: list[str] = []
    for item in response.json()["data"]:
        if item.get("type") != "message" or item.get("role") != "user":
            continue
        texts.append(
            "".join(
                block.get("text", "")
                for block in item.get("content", [])
                if block.get("type") == "input_text"
            )
        )
    return texts


def test_control_room_replies_stay_in_their_sessions_and_open_original_workspace(
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
    lane_grid = page.get_by_test_id("control-room-lanes")
    expect(lane_grid).to_have_attribute("data-density", "compact")
    assert (
        page.evaluate(
            """() => getComputedStyle(
            document.querySelector('[data-testid="control-room-lanes"]')
        ).gridTemplateColumns.split(' ').length"""
        )
        == 4
    )
    payment_lane = lanes.filter(has_text="Review payment safeguards")
    expect(payment_lane).to_be_visible()
    crm_lane = lanes.filter(has_text="Prepare CRM release")
    expect(crm_lane).to_be_visible()
    assert payment_lane.bounding_box()["height"] <= 500
    expect(payment_lane.get_by_text("Now", exact=True)).to_have_count(0)
    expect(payment_lane.get_by_text("Goal", exact=True)).to_have_count(0)
    expect(payment_lane.get_by_text("Proof", exact=True)).to_have_count(0)
    expect(payment_lane.get_by_text("Current session", exact=True)).to_have_count(0)
    expect(
        payment_lane.get_by_role("link", name="Open task: Review payment safeguards")
    ).to_be_visible()
    expect(payment_lane.get_by_text("Open task", exact=True)).to_have_count(0)
    expect(
        payment_lane.get_by_role("textbox", name="Reply to Review payment safeguards")
    ).to_have_attribute("rows", "1")
    reply_box = payment_lane.get_by_role(
        "textbox", name="Reply to Review payment safeguards"
    ).bounding_box()
    send_button = payment_lane.get_by_role("button", name="Send reply").bounding_box()
    assert reply_box is not None
    assert send_button is not None
    assert abs(reply_box["y"] - send_button["y"]) <= 1
    assert abs(reply_box["height"] - send_button["height"]) <= 1

    payment_reply = "Continue only the payment review"
    crm_reply = "Continue only the CRM release"
    payment_lane.get_by_role("textbox", name="Reply to Review payment safeguards").fill(
        payment_reply
    )
    payment_lane.get_by_role("button", name="Send reply").click()
    crm_lane.get_by_role("textbox", name="Reply to Prepare CRM release").fill(crm_reply)
    crm_lane.get_by_role("button", name="Send reply").click()

    payment_transcript = payment_lane.get_by_test_id("control-room-lane-transcript")
    crm_transcript = crm_lane.get_by_test_id("control-room-lane-transcript")
    expect(payment_transcript.get_by_text(payment_reply, exact=True)).to_be_visible(timeout=30_000)
    expect(crm_transcript.get_by_text(crm_reply, exact=True)).to_be_visible(timeout=30_000)
    expect(payment_transcript.get_by_text(crm_reply, exact=True)).to_have_count(0)
    expect(crm_transcript.get_by_text(payment_reply, exact=True)).to_have_count(0)

    payment_texts = _user_message_texts(base_url, session_a)
    crm_texts = _user_message_texts(base_url, session_b)
    assert payment_reply in payment_texts
    assert crm_reply not in payment_texts
    assert crm_reply in crm_texts
    assert payment_reply not in crm_texts

    crm_lane.get_by_role("link", name="Open task").click()
    expect(page).to_have_url(f"{base_url}/c/{session_b}")
    expect(page.get_by_label("Message the agent")).to_be_visible(timeout=30_000)

    expand_workspace = page.get_by_role("button", name="Expand right panel")
    if expand_workspace.is_visible():
        expand_workspace.click()
    workspace = page.get_by_label("Workspace")
    expect(workspace).to_be_visible(timeout=30_000)
    workspace.get_by_role("tab", name=re.compile(r"Session tree \d")).click()

    worker_summary = page.get_by_test_id("worker-sidebar-summary")
    expect(worker_summary).to_be_visible()
    expect(worker_summary.get_by_text("Prepare CRM release", exact=True)).to_be_visible()
    expect(worker_summary.get_by_text("1 worker", exact=True)).to_be_visible()
    expect(worker_summary.get_by_text("0/0 · Not started", exact=True)).to_be_visible()
    expect(
        worker_summary.get_by_role(
            "progressbar",
            name="Session implementation progress 0 of 0, Not started",
        )
    ).to_be_visible()
    expect(
        worker_summary.get_by_text(
            "Commit, push, and deploy still require your approval.", exact=True
        )
    ).to_be_visible()
