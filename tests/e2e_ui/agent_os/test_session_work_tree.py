"""Permanent browser journey for the durable session Work Tree.

Proves the property the whole feature exists for: one conversation keeps one
understandable Work Tree that survives a reload, a provider switch, and a
provider that forgets what it was doing.

Both providers are driven through the **real** native adapter event path —
``POST /v1/sessions/{id}/events`` with ``external_session_todos``, which is
exactly what ``claude_native_forwarder`` (from ``TodoWrite``) and
``codex_native_forwarder`` (from ``update_plan``) post. The live vendor CLIs are
not available in CI, so the remaining live acceptance step is named in the
module docstring rather than faked: run a real ``claude`` and a real ``codex``
session against a host and confirm the same tree assembles from their own
events.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from playwright.sync_api import Page, expect

_TIMEOUT_MS = 30_000


def _post_provider_todos(base_url: str, session_id: str, todos: list[dict[str, str]]) -> None:
    """Post a provider todo list through the real native-forwarder event path."""
    response = httpx.post(
        f"{base_url}/v1/sessions/{session_id}/events",
        json={"type": "external_session_todos", "data": {"todos": todos}},
        timeout=10.0,
    )
    response.raise_for_status()


def _todo(content: str, status: str) -> dict[str, str]:
    """One provider todo entry in the shape both forwarders post."""
    return {"content": content, "status": status, "activeForm": content}


def _work_tree(base_url: str, session_id: str) -> dict:
    """Read the durable tree the way a page reload does."""
    response = httpx.get(f"{base_url}/v1/sessions/{session_id}/work-tree", timeout=10.0)
    response.raise_for_status()
    return response.json()


def _titles(tree: dict) -> list[str]:
    """Item titles in display order."""
    return [item["title"] for item in tree["data"]]


def _by_title(tree: dict, title: str) -> dict:
    """Look one item up by exact title."""
    return next(item for item in tree["data"] if item["title"] == title)


def _open_work_tree_panel(page: Page, base_url: str, session_id: str) -> None:
    """Open a session and select the right rail's task surface."""
    page.goto(f"{base_url}/c/{session_id}")
    expand_workspace = page.get_by_role("button", name="Expand right panel")
    if expand_workspace.is_visible():
        expand_workspace.click()
    workspace = page.get_by_label("Workspace")
    expect(workspace).to_be_visible(timeout=_TIMEOUT_MS)
    workspace.get_by_role("tab", name="Tasks").click()
    expect(page.get_by_test_id("work-tree")).to_be_visible(timeout=_TIMEOUT_MS)


def test_work_tree_survives_reload_provider_switch_and_a_shorter_list(
    page: Page,
    seeded_session_pair: tuple[str, str, str],
    tmp_path: Path,
) -> None:
    """A Claude session and a Codex session each keep one durable tree.

    The journey walks the failure the durable tree replaces: a provider used to
    own the checklist, so anything it dropped disappeared. Here the user's own
    item and their delivery state outlive a shorter provider list, a provider
    switch, and a full page reload.
    """
    base_url, claude_session, codex_session = seeded_session_pair
    page.set_viewport_size({"width": 1600, "height": 1100})

    # ── a Claude session builds a tree from its own todo events ──
    _post_provider_todos(
        base_url,
        claude_session,
        [_todo("Read the payment code", "completed"), _todo("Write the fix", "in_progress")],
    )
    claude_tree = _work_tree(base_url, claude_session)
    assert _titles(claude_tree) == ["Read the payment code", "Write the fix"]
    assert _by_title(claude_tree, "Read the payment code")["status"] == "done"
    assert _by_title(claude_tree, "Write the fix")["status"] == "working"
    # A finished step never implies the change was shipped.
    assert _by_title(claude_tree, "Read the payment code")["delivery_state"] is None

    _open_work_tree_panel(page, base_url, claude_session)
    expect(page.get_by_text("Read the payment code")).to_be_visible(timeout=_TIMEOUT_MS)
    expect(page.get_by_text("Write the fix")).to_be_visible()
    page.screenshot(path=str(tmp_path / "work-tree-claude-session.png"), full_page=True)

    # ── the user adds their own item and records real delivery state ──
    created = httpx.post(
        f"{base_url}/v1/sessions/{claude_session}/work-items",
        json={
            "title": "Confirm with the payments team",
            "why": "They own the rollback plan",
            "evidence": "Asked in the release channel",
        },
        timeout=10.0,
    )
    created.raise_for_status()
    mine = created.json()

    shipped = httpx.patch(
        f"{base_url}/v1/sessions/{claude_session}/work-items/"
        f"{_by_title(claude_tree, 'Read the payment code')['id']}",
        json={
            "version": _by_title(claude_tree, "Read the payment code")["version"],
            "delivery_state": "merged",
        },
        timeout=10.0,
    )
    shipped.raise_for_status()

    # ── the provider forgets almost everything ──
    _post_provider_todos(base_url, claude_session, [_todo("Write the fix", "completed")])

    after = _work_tree(base_url, claude_session)
    # Nothing was deleted: the provider's shorter list only moved a status.
    assert set(_titles(after)) == {
        "Read the payment code",
        "Write the fix",
        "Confirm with the payments team",
    }
    survivor = _by_title(after, "Confirm with the payments team")
    assert survivor["id"] == mine["id"]
    assert survivor["why"] == "They own the rollback plan"
    assert survivor["evidence"] == "Asked in the release channel"
    assert _by_title(after, "Read the payment code")["delivery_state"] == "merged"
    assert _by_title(after, "Write the fix")["status"] == "done"

    # ── a full reload rebuilds the same tree from the durable endpoint ──
    page.reload()
    _open_work_tree_panel(page, base_url, claude_session)
    expect(page.get_by_text("Confirm with the payments team")).to_be_visible(timeout=_TIMEOUT_MS)
    merged_id = _by_title(after, "Read the payment code")["id"]
    expect(page.get_by_test_id(f"work-item-delivery-{merged_id}")).to_have_text("Merged")
    page.screenshot(path=str(tmp_path / "work-tree-after-reload.png"), full_page=True)

    # ── the other provider takes over the same work, ids intact ──
    ids_before = {item["title"]: item["id"] for item in after["data"]}
    _post_provider_todos(
        base_url,
        claude_session,
        [_todo("read the payment code.", "completed"), _todo("Write the fix", "completed")],
    )
    switched = _work_tree(base_url, claude_session)
    assert {item["title"]: item["id"] for item in switched["data"]} == ids_before

    # ── a second, independent session keeps its own tree ──
    _post_provider_todos(
        base_url,
        codex_session,
        [_todo("Draft the CRM release notes", "in_progress")],
    )
    codex_tree = _work_tree(base_url, codex_session)
    assert _titles(codex_tree) == ["Draft the CRM release notes"]
    # One session's work never leaks into another's tree.
    assert "Confirm with the payments team" not in _titles(codex_tree)

    _open_work_tree_panel(page, base_url, codex_session)
    expect(page.get_by_text("Draft the CRM release notes")).to_be_visible(timeout=_TIMEOUT_MS)
    expect(page.get_by_text("Confirm with the payments team")).to_have_count(0)
    page.screenshot(path=str(tmp_path / "work-tree-codex-session.png"), full_page=True)


def test_a_session_with_no_meaningful_plan_shows_no_work_tree(
    page: Page,
    seeded_session_pair: tuple[str, str, str],
) -> None:
    """Casual conversation must not manufacture a tree.

    An empty provider list is a real event — a session that opened and said
    nothing useful. It has to leave the rail alone rather than creating an
    empty scaffold the user then has to dismiss.
    """
    base_url, session_id, _ = seeded_session_pair
    _post_provider_todos(base_url, session_id, [])

    tree = _work_tree(base_url, session_id)
    assert tree["data"] == []

    page.goto(f"{base_url}/c/{session_id}")
    expand_workspace = page.get_by_role("button", name="Expand right panel")
    if expand_workspace.is_visible():
        expand_workspace.click()
    workspace = page.get_by_label("Workspace")
    expect(workspace).to_be_visible(timeout=_TIMEOUT_MS)
    # No tracked work means no Tasks tab at all.
    expect(workspace.get_by_role("tab", name="Tasks")).to_have_count(0)
