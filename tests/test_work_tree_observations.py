"""Tests for provider todo/plan observations reconciling into the Work Tree.

The point of the durable tree is that a provider cannot destroy user context.
These tests drive the same provider-neutral owner both native adapters feed —
Claude ``TodoWrite`` and Codex ``update_plan`` arrive in one shared shape — and
pin what an observation is and is not allowed to do.
"""

from __future__ import annotations

import uuid

import pytest

from omnigent.stores.work_tree_store.sqlalchemy_store import SqlAlchemyWorkTreeStore
from omnigent.work_tree_observations import (
    apply_provider_todos,
    observations_from_provider_todos,
)

SESSION = uuid.uuid5(uuid.NAMESPACE_DNS, "observations-session").hex


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyWorkTreeStore:
    """A fresh work tree store backed by the test SQLite DB."""
    return SqlAlchemyWorkTreeStore(db_uri)


def _todo(content: str, status: str = "pending", **extra: object) -> dict:
    """Build one provider todo entry in the shared forwarder shape."""
    entry: dict = {"content": content, "status": status, "activeForm": content}
    entry.update(extra)  # type: ignore[arg-type]
    return entry


def _titles(store: SqlAlchemyWorkTreeStore) -> list[str]:
    """Current tree titles, in display order."""
    return [i.title for i in store.list_tree(SESSION)]


def _by_title(store: SqlAlchemyWorkTreeStore, title: str):
    """Look one item up by exact title."""
    return next(i for i in store.list_tree(SESSION) if i.title == title)


# ── translation ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("provider_status", "expected"),
    [("pending", "not_started"), ("in_progress", "working"), ("completed", "done")],
)
def test_provider_statuses_map_onto_the_work_axis(provider_status: str, expected: str) -> None:
    """The three provider statuses become work statuses, never delivery states."""
    [observation] = observations_from_provider_todos(
        [_todo("Task", provider_status)], provider="claude"
    )
    assert observation.status == expected


def test_malformed_entries_are_skipped(store: SqlAlchemyWorkTreeStore) -> None:
    """A buggy forwarder version cannot inject junk into the tree."""
    observations = observations_from_provider_todos(
        [
            "not a dict",  # type: ignore[list-item]
            {"content": "", "status": "pending"},
            {"content": "Valid", "status": "nonsense"},
            {"status": "pending"},
            _todo("Real task"),
        ],
        provider="claude",
    )
    assert [o.title for o in observations] == ["Real task"]


def test_a_provider_step_id_is_namespaced(store: SqlAlchemyWorkTreeStore) -> None:
    """A provider-supplied id is kept, prefixed so two providers cannot collide."""
    [observation] = observations_from_provider_todos(
        [_todo("Task", id="step-1")], provider="codex"
    )
    assert observation.source_ref == "codex:step-1"


# ── create / update ───────────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_first_observation_creates_the_tree(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """Either provider's first list becomes provider-sourced nodes."""
    changed = apply_provider_todos(
        store, SESSION, [_todo("Read the code"), _todo("Write the fix")], provider=provider
    )
    assert changed is True
    assert _titles(store) == ["Read the code", "Write the fix"]
    assert {i.source_kind for i in store.list_tree(SESSION)} == {"provider_todo"}


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_later_observation_advances_status_in_place(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """The same step moving to in-progress updates the node, not a duplicate."""
    apply_provider_todos(store, SESSION, [_todo("Read the code")], provider=provider)
    first_id = store.list_tree(SESSION)[0].id

    apply_provider_todos(
        store, SESSION, [_todo("Read the code", "in_progress")], provider=provider
    )
    tree = store.list_tree(SESSION)
    assert len(tree) == 1
    assert tree[0].id == first_id
    assert tree[0].status == "working"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_completion_sets_done_but_never_a_delivery_state(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """A provider may finish the work; it may not claim the change shipped."""
    apply_provider_todos(store, SESSION, [_todo("Ship it")], provider=provider)
    apply_provider_todos(store, SESSION, [_todo("Ship it", "completed")], provider=provider)
    item = _by_title(store, "Ship it")
    assert item.status == "done"
    assert item.delivery_state is None


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_an_unchanged_list_reports_no_change(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """Re-sending the same list is a no-op, so nothing is republished."""
    apply_provider_todos(store, SESSION, [_todo("Task")], provider=provider)
    assert apply_provider_todos(store, SESSION, [_todo("Task")], provider=provider) is False


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_reordered_list_does_not_duplicate_or_drop(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """Reordering the provider's list re-matches the same nodes."""
    apply_provider_todos(store, SESSION, [_todo("A"), _todo("B"), _todo("C")], provider=provider)
    ids = {i.title: i.id for i in store.list_tree(SESSION)}

    apply_provider_todos(store, SESSION, [_todo("C"), _todo("A"), _todo("B")], provider=provider)
    assert {i.title: i.id for i in store.list_tree(SESSION)} == ids


# ── what an observation must never destroy ────────────────────────────────


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_shorter_list_deletes_nothing(store: SqlAlchemyWorkTreeStore, provider: str) -> None:
    """A provider that forgets a step must not take it out of the tree.

    This is the failure the durable tree exists to prevent: a compaction or a
    restart shortens the provider's list, and the user's work silently vanishes.
    """
    apply_provider_todos(store, SESSION, [_todo("Keep me"), _todo("And me")], provider=provider)
    apply_provider_todos(store, SESSION, [_todo("Keep me")], provider=provider)
    assert _titles(store) == ["Keep me", "And me"]


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_an_empty_list_changes_nothing(store: SqlAlchemyWorkTreeStore, provider: str) -> None:
    """An empty or all-malformed observation leaves the tree untouched."""
    apply_provider_todos(store, SESSION, [_todo("Existing work")], provider=provider)
    assert apply_provider_todos(store, SESSION, [], provider=provider) is False
    assert _titles(store) == ["Existing work"]


def test_casual_conversation_does_not_manufacture_a_tree(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """No meaningful todo means no tree at all, not an empty placeholder."""
    assert apply_provider_todos(store, SESSION, [], provider="claude") is False
    assert store.list_tree(SESSION) == []


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_manual_wording_and_context_survive_an_observation(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """A provider may move the status; the user's words stay the user's words."""
    item = store.create(
        SESSION,
        title="Fix the login bug",
        brief="Reported by support on Tuesday",
        why="Blocks three customers",
        evidence="Reproduced locally",
        actor="alice@example.com",
    )
    apply_provider_todos(
        store, SESSION, [_todo("fix the login bug.", "completed")], provider=provider
    )
    after = store.get(SESSION, item.id)
    assert after.status == "done"
    assert after.title == "Fix the login bug"
    assert after.brief == "Reported by support on Tuesday"
    assert after.why == "Blocks three customers"
    assert after.evidence == "Reproduced locally"
    assert after.source_kind == "user"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_delivery_state_survives_an_observation(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """A merged item stays merged no matter what the provider's list says."""
    item = store.create(SESSION, title="Ship the fix")
    store.update(SESSION, item.id, expected_version=item.version, delivery_state="merged")
    apply_provider_todos(store, SESSION, [_todo("Ship the fix")], provider=provider)
    assert store.get(SESSION, item.id).delivery_state == "merged"


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_children_survive_an_observation(store: SqlAlchemyWorkTreeStore, provider: str) -> None:
    """Matching a parent never disturbs the subtasks hanging off it."""
    parent = store.create(SESSION, title="Refactor the store")
    store.create(SESSION, title="Extract the helper", parent_id=parent.id)
    apply_provider_todos(
        store, SESSION, [_todo("Refactor the store", "completed")], provider=provider
    )
    tree = store.list_tree(SESSION)
    assert [i.title for i in tree] == ["Refactor the store", "Extract the helper"]


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_deferred_item_is_not_resurrected(store: SqlAlchemyWorkTreeStore, provider: str) -> None:
    """Something the user parked stays parked until the user says otherwise."""
    item = store.create(SESSION, title="Later work")
    store.defer(SESSION, item.id, expected_version=item.version)
    apply_provider_todos(store, SESSION, [_todo("Later work", "in_progress")], provider=provider)
    after = store.get(SESSION, item.id)
    assert after.status == "paused"
    assert after.deferred_at is not None


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_blocked_item_is_not_quietly_unblocked(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """ "Blocked" is a person's judgement the provider cannot see."""
    item = store.create(SESSION, title="Waiting on infra")
    store.update(SESSION, item.id, expected_version=item.version, status="blocked")
    apply_provider_todos(
        store, SESSION, [_todo("Waiting on infra", "in_progress")], provider=provider
    )
    assert store.get(SESSION, item.id).status == "blocked"


# ── stability across restarts and provider switches ───────────────────────


def test_ids_survive_a_provider_switch(store: SqlAlchemyWorkTreeStore) -> None:
    """Handing the same work to the other provider keeps the same nodes.

    Without this the tree would fork every time the session changed model or
    harness, and the user's history would scatter across duplicates.
    """
    apply_provider_todos(
        store, SESSION, [_todo("Read the code"), _todo("Write the fix")], provider="claude"
    )
    before = {i.title: i.id for i in store.list_tree(SESSION)}

    apply_provider_todos(
        store,
        SESSION,
        [_todo("Read the code", "completed"), _todo("Write the fix", "in_progress")],
        provider="codex",
    )
    after = store.list_tree(SESSION)
    assert {i.title: i.id for i in after} == before
    assert [i.status for i in after] == ["done", "working"]


def test_ids_survive_a_worker_restart_with_rephrased_steps(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """Punctuation and casing drift across a restart still re-match."""
    apply_provider_todos(store, SESSION, [_todo("Fix the login bug")], provider="claude")
    original = store.list_tree(SESSION)[0].id

    apply_provider_todos(
        store, SESSION, [_todo("fix the login bug!", "completed")], provider="claude"
    )
    tree = store.list_tree(SESSION)
    assert len(tree) == 1
    assert tree[0].id == original
    assert tree[0].status == "done"


@pytest.mark.parametrize(
    ("script", "title"),
    [
        ("japanese", "ログイン修正"),
        ("chinese", "修复登录问题"),
        ("korean", "로그인 수정"),
        ("cyrillic", "Исправить вход"),
        ("arabic", "إصلاح الدخول"),
        ("hebrew", "תיקון הכניסה"),
        ("thai", "แก้ไขการเข้าสู่ระบบ"),
        ("greek", "Διόρθωση σύνδεσης"),
        ("latin-control", "Fix login"),
    ],
)
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_repeated_non_latin_step_stays_one_item(
    store: SqlAlchemyWorkTreeStore, provider: str, script: str, title: str
) -> None:
    """A title in any script re-matches its own node instead of duplicating.

    Claude re-emits its list many times per turn. A title whose characters are
    all outside ``[a-z0-9]`` used to reduce to one unmatchable key, so every
    emission created another node and the rail grew without bound — permanently,
    because observations can never delete. Four identical batches must leave
    exactly one item, in every script.
    """
    for _ in range(4):
        apply_provider_todos(store, SESSION, [_todo(title)], provider=provider)
    assert _titles(store) == [title]


def test_non_latin_titles_keep_stable_ids_across_a_provider_switch(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """The id-stability guarantee holds for non-Latin scripts too."""
    apply_provider_todos(store, SESSION, [_todo("ログイン修正")], provider="claude")
    original = store.list_tree(SESSION)[0].id

    apply_provider_todos(store, SESSION, [_todo("ログイン修正", "completed")], provider="codex")
    tree = store.list_tree(SESSION)
    assert [i.id for i in tree] == [original]
    assert tree[0].status == "done"


def test_a_fullwidth_rephrasing_rematches_the_same_node(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """NFKC folding means a full-width spelling is the same step, not a new one."""
    apply_provider_todos(store, SESSION, [_todo("Fix login")], provider="claude")
    original = store.list_tree(SESSION)[0].id

    apply_provider_todos(store, SESSION, [_todo("Ｆｉｘ　ｌｏｇｉｎ")], provider="claude")
    assert [i.id for i in store.list_tree(SESSION)] == [original]


def test_a_symbol_only_step_rematches_itself_and_nothing_else(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """Emoji- and punctuation-only titles re-match verbatim, never each other.

    These keep no word characters at all, so they fall back to comparing the
    literal text. That must be exact: a duplicate-free tree is worth nothing if
    "🎉" quietly starts driving the status of "✅".
    """
    for _ in range(4):
        apply_provider_todos(
            store, SESSION, [_todo("🎉"), _todo("✅"), _todo("---")], provider="claude"
        )
    assert _titles(store) == ["🎉", "✅", "---"]

    apply_provider_todos(store, SESSION, [_todo("🎉", "completed")], provider="claude")
    assert [(i.title, i.status) for i in store.list_tree(SESSION)] == [
        ("🎉", "done"),
        ("✅", "not_started"),
        ("---", "not_started"),
    ]


# ── work the user finished stays finished ─────────────────────────────────


@pytest.mark.parametrize("provider", ["claude", "codex"])
@pytest.mark.parametrize("observed", ["pending", "in_progress"])
def test_a_user_marked_done_item_is_not_reopened_by_an_observation(
    store: SqlAlchemyWorkTreeStore, provider: str, observed: str
) -> None:
    """A tick the user made survives the provider re-emitting the step.

    Reopening it would also clear ``completed_at``, so the session would lose
    both the decision and the record of when it was made.
    """
    item = store.create(SESSION, title="Fix the login bug", actor="alice@example.com")
    store.update(
        SESSION,
        item.id,
        expected_version=item.version,
        status="done",
        actor="alice@example.com",
    )
    completed_at = store.get(SESSION, item.id).completed_at

    apply_provider_todos(store, SESSION, [_todo("Fix the login bug", observed)], provider=provider)
    after = store.get(SESSION, item.id)
    assert after.status == "done"
    assert after.completed_at == completed_at


def test_a_user_completion_survives_a_restart_and_a_provider_switch(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """The whole restart sequence — both providers, repeatedly — leaves it done."""
    item = store.create(SESSION, title="Ship the fix", actor="alice@example.com")
    store.update(
        SESSION,
        item.id,
        expected_version=item.version,
        status="done",
        actor="alice@example.com",
    )
    completed_at = store.get(SESSION, item.id).completed_at

    for provider in ("claude", "codex", "claude"):
        apply_provider_todos(store, SESSION, [_todo("Ship the fix")], provider=provider)
        apply_provider_todos(
            store, SESSION, [_todo("Ship the fix", "in_progress")], provider=provider
        )

    after = store.get(SESSION, item.id)
    assert after.status == "done"
    assert after.completed_at == completed_at
    assert len(store.list_tree(SESSION)) == 1


def test_a_user_completion_of_a_provider_created_item_also_survives(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """Who created the node does not matter; who finished it does."""
    apply_provider_todos(store, SESSION, [_todo("Read the code")], provider="claude")
    item = store.list_tree(SESSION)[0]
    store.update(
        SESSION,
        item.id,
        expected_version=item.version,
        status="done",
        actor="alice@example.com",
    )

    apply_provider_todos(store, SESSION, [_todo("Read the code")], provider="claude")
    assert store.get(SESSION, item.id).status == "done"


def test_a_provider_may_still_reopen_its_own_completion(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """Provider-owned progress is preserved — only a person's tick is frozen.

    A provider that marked a step done and then goes back to it is reporting
    real progress about its own work, and the tree should follow.
    """
    apply_provider_todos(store, SESSION, [_todo("Run the tests", "completed")], provider="claude")
    item = store.list_tree(SESSION)[0]
    assert item.status == "done"

    apply_provider_todos(
        store, SESSION, [_todo("Run the tests", "in_progress")], provider="claude"
    )
    assert store.get(SESSION, item.id).status == "working"


# ── a flat provider list only ever means root steps ───────────────────────


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_provider_step_does_not_hijack_a_same_titled_subtask(
    store: SqlAlchemyWorkTreeStore, provider: str
) -> None:
    """A flat provider step must not bind to a person's nested subtask.

    Providers emit a flat list, so a match at depth 2 or 3 is a coincidence of
    wording — and acting on it would drive the status of work filed under
    something else entirely.
    """
    parent = store.create(SESSION, title="Rewrite the auth module")
    nested = store.create(
        SESSION, title="Fix login", parent_id=parent.id, actor="alice@example.com"
    )

    apply_provider_todos(store, SESSION, [_todo("Fix login", "completed")], provider=provider)

    assert store.get(SESSION, nested.id).status == "not_started"
    roots = [i for i in store.list_tree(SESSION) if i.depth == 1]
    created = next(i for i in roots if i.title == "Fix login")
    assert created.source_kind == "provider_todo"
    assert created.status == "done"


def test_a_root_user_item_is_still_adopted(store: SqlAlchemyWorkTreeStore) -> None:
    """Restricting matches to root does not stop a top-level user item matching."""
    item = store.create(SESSION, title="Fix login", actor="alice@example.com")
    apply_provider_todos(store, SESSION, [_todo("Fix login", "in_progress")], provider="claude")
    assert [i.id for i in store.list_tree(SESSION)] == [item.id]
    assert store.get(SESSION, item.id).status == "working"


def test_an_observation_is_recorded_as_a_provider_actor(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """The audit trail attributes provider-driven changes to the provider."""
    apply_provider_todos(store, SESSION, [_todo("Task")], provider="claude")
    item = store.list_tree(SESSION)[0]
    assert [e.actor for e in store.list_events(SESSION, item.id)] == ["provider:claude"]
