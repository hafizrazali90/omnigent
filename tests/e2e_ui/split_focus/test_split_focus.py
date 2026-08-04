"""UI journey: keep two complete task workspaces open without state leakage.

The sessions are created through Omnigent's real API. Split Focus loads each
normal conversation workspace in its own browser realm, sends a different
instruction through each real composer, proves the server stored each message
under only its intended session, and verifies the URL restores both panes.
"""

from __future__ import annotations

import httpx
from playwright.sync_api import FrameLocator, Page, expect


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


def _send_message(workspace: FrameLocator, message: str) -> None:
    composer = workspace.get_by_label("Message the agent")
    expect(composer).to_be_visible(timeout=30_000)
    composer.fill(message)
    workspace.get_by_role("button", name="Send", exact=True).click()
    expect(workspace.get_by_text(message, exact=True)).to_be_visible(timeout=30_000)


def _wait_for_stored_message(
    page: Page,
    base_url: str,
    session_id: str,
    message: str,
) -> list[str]:
    texts: list[str] = []
    for _ in range(100):
        texts = _user_message_texts(base_url, session_id)
        if message in texts:
            return texts
        page.wait_for_timeout(100)
    raise AssertionError(
        f"message was visible in the workspace but not stored for {session_id}: {message}"
    )


def test_split_focus_keeps_complete_task_workspaces_independent_and_restores_them(
    page: Page,
    seeded_session_pair: tuple[str, str, str],
) -> None:
    base_url, session_a, session_b = seeded_session_pair
    payment_title = "Review payment safeguards"
    crm_title = "Prepare CRM release"
    _title_session(base_url, session_a, payment_title)
    _title_session(base_url, session_b, crm_title)
    page.set_viewport_size({"width": 2200, "height": 1200})

    page.goto(f"{base_url}/split-focus?session={session_a}&session={session_b}")

    expect(page.get_by_role("heading", name="Split Focus")).to_be_visible(timeout=30_000)
    expect(page.get_by_test_id("split-focus-pane")).to_have_count(2, timeout=30_000)
    payment_workspace = page.frame_locator(f'iframe[title="Task workspace: {payment_title}"]')
    crm_workspace = page.frame_locator(f'iframe[title="Task workspace: {crm_title}"]')
    expect(payment_workspace.get_by_label("Message the agent")).to_be_visible(timeout=30_000)
    expect(crm_workspace.get_by_label("Message the agent")).to_be_visible(timeout=30_000)
    expect(payment_workspace.get_by_label("Conversations")).to_have_count(0)
    expect(crm_workspace.get_by_label("Conversations")).to_have_count(0)

    payment_message = "Continue only the payment safeguard task"
    crm_message = "Continue only the CRM release task"
    _send_message(payment_workspace, payment_message)
    _send_message(crm_workspace, crm_message)

    payment_texts = _wait_for_stored_message(page, base_url, session_a, payment_message)
    crm_texts = _wait_for_stored_message(page, base_url, session_b, crm_message)
    assert payment_message in payment_texts
    assert crm_message not in payment_texts
    assert crm_message in crm_texts
    assert payment_message not in crm_texts

    page.reload()
    expect(page.get_by_test_id("split-focus-pane")).to_have_count(2, timeout=30_000)
    expect(
        page.frame_locator(f'iframe[title="Task workspace: {payment_title}"]').get_by_text(
            payment_message, exact=True
        )
    ).to_be_visible(timeout=30_000)
    expect(
        page.frame_locator(f'iframe[title="Task workspace: {crm_title}"]').get_by_text(
            crm_message, exact=True
        )
    ).to_be_visible(timeout=30_000)
