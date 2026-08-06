"""Tests for :class:`SqlAlchemyWorkTreeStore`.

Exercises the durable session Work Tree against a real SQLite database. The
cases here are the product rules that make the tree trustworthy — session
scoping, the three-level cap, optimistic versioning, a delete that refuses to
cascade, atomic defer/resume, and a sanitized audit trail — because everything
above this layer (routes, provider adapters, the web rail) relies on the store
to hold them.
"""

from __future__ import annotations

import threading
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from omnigent.entities.work_item import WorkItem
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.work_tree_store.sqlalchemy_store import SqlAlchemyWorkTreeStore

SESSION = uuid.uuid5(uuid.NAMESPACE_DNS, "work-tree-session").hex
OTHER_SESSION = uuid.uuid5(uuid.NAMESPACE_DNS, "work-tree-other-session").hex
PROJECT = uuid.uuid5(uuid.NAMESPACE_DNS, "work-tree-project").hex


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyWorkTreeStore:
    """A fresh :class:`SqlAlchemyWorkTreeStore` backed by the test SQLite DB.

    :param db_uri: Per-test SQLite URI from the root conftest fixture.
    :returns: A ready-to-use :class:`SqlAlchemyWorkTreeStore` instance.
    """
    return SqlAlchemyWorkTreeStore(db_uri)


# ── empty tree / create ───────────────────────────────────────────────────


def test_session_without_items_reads_as_empty_tree(store: SqlAlchemyWorkTreeStore) -> None:
    """A session that has never had an item reads as ``[]``, not an error.

    This is the migrated state of every session that predates the Work Tree.
    """
    assert store.list_tree(SESSION) == []


def test_create_returns_item_with_defaults(store: SqlAlchemyWorkTreeStore) -> None:
    """``create`` echoes the fields back with version 1 and the root depth."""
    item = store.create(SESSION, title="Ship the work tree", actor="alice@example.com")
    assert item.title == "Ship the work tree"
    assert item.conversation_id == SESSION
    assert item.parent_id is None
    assert item.depth == 1
    assert item.status == "not_started"
    assert item.delivery_state is None
    assert item.source_kind == "user"
    assert item.version == 1
    assert item.created_at > 0
    assert item.updated_at is None


def test_create_rejects_a_blank_title(store: SqlAlchemyWorkTreeStore) -> None:
    """A title of only whitespace is refused rather than stored empty."""
    with pytest.raises(OmnigentError) as excinfo:
        store.create(SESSION, title="   ")
    assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_create_rejects_an_unknown_status(store: SqlAlchemyWorkTreeStore) -> None:
    """An out-of-vocabulary status fails at the codec rather than being stored."""
    with pytest.raises(ValueError):
        store.create(SESSION, title="Bad", status="almost_done")


# ── tree shape ────────────────────────────────────────────────────────────


def test_children_nest_under_their_parent_in_order(store: SqlAlchemyWorkTreeStore) -> None:
    """``list_tree`` returns parents before children, siblings in sort order."""
    parent = store.create(SESSION, title="Task")
    first = store.create(SESSION, title="Subtask A", parent_id=parent.id)
    second = store.create(SESSION, title="Subtask B", parent_id=parent.id)
    assert [i.id for i in store.list_tree(SESSION)] == [parent.id, first.id, second.id]
    assert [i.depth for i in store.list_tree(SESSION)] == [1, 2, 2]


def test_depth_four_fails_explicitly(store: SqlAlchemyWorkTreeStore) -> None:
    """The tree stops at session → task → subtask; a fourth level is refused."""
    level1 = store.create(SESSION, title="Programme")
    level2 = store.create(SESSION, title="Task", parent_id=level1.id)
    level3 = store.create(SESSION, title="Subtask", parent_id=level2.id)
    with pytest.raises(OmnigentError) as excinfo:
        store.create(SESSION, title="Too deep", parent_id=level3.id)
    assert excinfo.value.code == ErrorCode.INVALID_INPUT
    assert len(store.list_tree(SESSION)) == 3


def test_parent_from_another_session_is_not_found(store: SqlAlchemyWorkTreeStore) -> None:
    """A parent and child must belong to the same session."""
    foreign = store.create(OTHER_SESSION, title="Someone else's task")
    with pytest.raises(OmnigentError) as excinfo:
        store.create(SESSION, title="Child", parent_id=foreign.id)
    assert excinfo.value.code == ErrorCode.NOT_FOUND


def test_get_does_not_leak_across_sessions(store: SqlAlchemyWorkTreeStore) -> None:
    """An item is invisible to any session that does not own it."""
    item = store.create(OTHER_SESSION, title="Private")
    assert store.get(OTHER_SESSION, item.id) is not None
    assert store.get(SESSION, item.id) is None


def test_list_tree_is_scoped_to_one_session(store: SqlAlchemyWorkTreeStore) -> None:
    """One session's items never appear in another's tree."""
    store.create(SESSION, title="Mine")
    store.create(OTHER_SESSION, title="Theirs")
    assert [i.title for i in store.list_tree(SESSION)] == ["Mine"]
    assert [i.title for i in store.list_tree(OTHER_SESSION)] == ["Theirs"]


# ── optimistic concurrency ────────────────────────────────────────────────


def test_update_bumps_the_version(store: SqlAlchemyWorkTreeStore) -> None:
    """A successful update advances the version so the next writer must re-read."""
    item = store.create(SESSION, title="Task")
    updated = store.update(SESSION, item.id, expected_version=item.version, status="working")
    assert updated.status == "working"
    assert updated.version == item.version + 1
    assert updated.updated_at is not None


def test_stale_update_is_a_conflict_and_changes_nothing(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """A second window writing against the version it last saw is refused.

    Without this, the later write would silently overwrite the earlier one and
    the user would never learn their edit was lost.
    """
    item = store.create(SESSION, title="Task")
    store.update(SESSION, item.id, expected_version=item.version, title="First writer wins")
    with pytest.raises(OmnigentError) as excinfo:
        store.update(SESSION, item.id, expected_version=item.version, title="Second writer")
    assert excinfo.value.code == ErrorCode.CONFLICT
    assert store.get(SESSION, item.id).title == "First writer wins"


def test_update_rejects_an_unknown_field(store: SqlAlchemyWorkTreeStore) -> None:
    """An unknown field is an explicit error, never a silent no-op."""
    item = store.create(SESSION, title="Task")
    with pytest.raises(OmnigentError) as excinfo:
        store.update(SESSION, item.id, expected_version=item.version, sneaky="value")
    assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_update_on_another_session_is_not_found(store: SqlAlchemyWorkTreeStore) -> None:
    """Writing to an item through the wrong session is a 404, not a cross-write."""
    item = store.create(OTHER_SESSION, title="Private")
    with pytest.raises(OmnigentError) as excinfo:
        store.update(SESSION, item.id, expected_version=item.version, status="done")
    assert excinfo.value.code == ErrorCode.NOT_FOUND


# ── the two axes stay separate ────────────────────────────────────────────


def test_status_done_does_not_set_a_delivery_state(store: SqlAlchemyWorkTreeStore) -> None:
    """Finishing the work never implies the change was committed or shipped."""
    item = store.create(SESSION, title="Task")
    done = store.update(SESSION, item.id, expected_version=item.version, status="done")
    assert done.status == "done"
    assert done.delivery_state is None
    assert done.completed_at is not None


def test_reopening_an_item_clears_completed_at(store: SqlAlchemyWorkTreeStore) -> None:
    """A reopened item must not keep a stamp that reads as finished."""
    item = store.create(SESSION, title="Task")
    done = store.update(SESSION, item.id, expected_version=item.version, status="done")
    reopened = store.update(SESSION, item.id, expected_version=done.version, status="working")
    assert reopened.completed_at is None


def test_delivery_state_moves_independently_of_status(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """Delivery state is set explicitly and does not disturb the work status."""
    item = store.create(SESSION, title="Task")
    shipped = store.update(
        SESSION, item.id, expected_version=item.version, delivery_state="merged"
    )
    assert shipped.delivery_state == "merged"
    assert shipped.status == "not_started"


# ── reorder ───────────────────────────────────────────────────────────────


def test_reorder_moves_only_siblings(store: SqlAlchemyWorkTreeStore) -> None:
    """Reordering renumbers the moved item's siblings and nothing else."""
    first = store.create(SESSION, title="A")
    second = store.create(SESSION, title="B")
    third = store.create(SESSION, title="C")
    child = store.create(SESSION, title="A-1", parent_id=first.id)

    store.reorder(SESSION, third.id, new_index=0, expected_version=third.version)

    roots = [i for i in store.list_tree(SESSION) if i.parent_id is None]
    assert [i.title for i in roots] == ["C", "A", "B"]
    assert store.get(SESSION, child.id).sort_order == 0
    assert store.get(SESSION, child.id).parent_id == first.id
    assert second.id in {i.id for i in roots}


def test_reorder_clamps_an_out_of_range_index(store: SqlAlchemyWorkTreeStore) -> None:
    """An index past the end lands the item last rather than failing."""
    first = store.create(SESSION, title="A")
    store.create(SESSION, title="B")
    store.reorder(SESSION, first.id, new_index=99, expected_version=first.version)
    roots = [i.title for i in store.list_tree(SESSION) if i.parent_id is None]
    assert roots == ["B", "A"]


def test_stale_reorder_is_a_conflict(store: SqlAlchemyWorkTreeStore) -> None:
    """Reorder honours the same version check as every other mutation."""
    item = store.create(SESSION, title="A")
    store.update(SESSION, item.id, expected_version=item.version, status="working")
    with pytest.raises(OmnigentError) as excinfo:
        store.reorder(SESSION, item.id, new_index=0, expected_version=item.version)
    assert excinfo.value.code == ErrorCode.CONFLICT


# ── delete ────────────────────────────────────────────────────────────────


def test_delete_removes_a_childless_item(store: SqlAlchemyWorkTreeStore) -> None:
    """A leaf deletes cleanly."""
    item = store.create(SESSION, title="Task")
    store.delete(SESSION, item.id, expected_version=item.version)
    assert store.list_tree(SESSION) == []


def test_delete_refuses_to_cascade(store: SqlAlchemyWorkTreeStore) -> None:
    """Deleting a parent fails safely instead of silently taking its children.

    A cascade here would destroy user briefs and evidence the parent row knows
    nothing about, so the caller has to deal with the children deliberately.
    """
    parent = store.create(SESSION, title="Task")
    child = store.create(SESSION, title="Subtask", parent_id=parent.id)
    with pytest.raises(OmnigentError) as excinfo:
        store.delete(SESSION, parent.id, expected_version=parent.version)
    assert excinfo.value.code == ErrorCode.CONFLICT
    assert {i.id for i in store.list_tree(SESSION)} == {parent.id, child.id}


def test_stale_delete_is_a_conflict(store: SqlAlchemyWorkTreeStore) -> None:
    """Delete honours the version check too."""
    item = store.create(SESSION, title="Task")
    store.update(SESSION, item.id, expected_version=item.version, status="working")
    with pytest.raises(OmnigentError) as excinfo:
        store.delete(SESSION, item.id, expected_version=item.version)
    assert excinfo.value.code == ErrorCode.CONFLICT
    assert store.get(SESSION, item.id) is not None


# ── defer / resume ────────────────────────────────────────────────────────


def test_defer_sets_status_and_stamp_together(store: SqlAlchemyWorkTreeStore) -> None:
    """Defer is atomic: an item is never paused without its deferred stamp."""
    item = store.create(SESSION, title="Task")
    deferred = store.defer(SESSION, item.id, expected_version=item.version)
    assert deferred.status == "paused"
    assert deferred.deferred_at is not None


def test_resume_clears_the_deferred_stamp(store: SqlAlchemyWorkTreeStore) -> None:
    """Resume returns the item to working and drops the stamp in one step."""
    item = store.create(SESSION, title="Task")
    deferred = store.defer(SESSION, item.id, expected_version=item.version)
    resumed = store.resume(SESSION, item.id, expected_version=deferred.version)
    assert resumed.status == "working"
    assert resumed.deferred_at is None


def test_stale_defer_is_a_conflict(store: SqlAlchemyWorkTreeStore) -> None:
    """Defer honours the version check."""
    item = store.create(SESSION, title="Task")
    store.update(SESSION, item.id, expected_version=item.version, status="working")
    with pytest.raises(OmnigentError) as excinfo:
        store.defer(SESSION, item.id, expected_version=item.version)
    assert excinfo.value.code == ErrorCode.CONFLICT


# ── audit trail ───────────────────────────────────────────────────────────


def test_every_mutation_records_an_audit_entry(store: SqlAlchemyWorkTreeStore) -> None:
    """Create, edit, reorder, defer and resume each leave a trail entry."""
    item = store.create(SESSION, title="Task", actor="alice@example.com")
    updated = store.update(
        SESSION,
        item.id,
        expected_version=item.version,
        status="working",
        actor="alice@example.com",
    )
    reordered = store.reorder(
        SESSION, item.id, new_index=0, expected_version=updated.version, actor="alice@example.com"
    )
    deferred = store.defer(
        SESSION, item.id, expected_version=reordered.version, actor="alice@example.com"
    )
    store.resume(SESSION, item.id, expected_version=deferred.version, actor="alice@example.com")

    actions = [e.action for e in store.list_events(SESSION, item.id)]
    assert actions == ["created", "status_changed", "reordered", "deferred", "resumed"]


def test_audit_records_actor_and_resulting_version(store: SqlAlchemyWorkTreeStore) -> None:
    """The trail says who acted and which version the item reached."""
    item = store.create(SESSION, title="Task", actor="alice@example.com")
    store.update(SESSION, item.id, expected_version=item.version, status="done", actor="claude")
    events = store.list_events(SESSION, item.id)
    assert [(e.actor, e.version) for e in events] == [("alice@example.com", 1), ("claude", 2)]


def test_audit_survives_the_item_it_describes(store: SqlAlchemyWorkTreeStore) -> None:
    """Deleting an item keeps its history — the delete itself is auditable."""
    item = store.create(SESSION, title="Task")
    store.delete(SESSION, item.id, expected_version=item.version)
    assert [e.action for e in store.list_events(SESSION, item.id)] == ["created", "deleted"]


def test_audit_summary_holds_no_free_text_payload(store: SqlAlchemyWorkTreeStore) -> None:
    """The trail stores which fields changed, not the values written into them.

    A summary that echoed field values would quietly turn the audit table into a
    copy of every title, brief and note the user ever typed.
    """
    item = store.create(SESSION, title="Task", why="because")
    store.update(
        SESSION,
        item.id,
        expected_version=item.version,
        brief="an internal note nobody should find in the audit table",
    )
    summaries = [e.summary for e in store.list_events(SESSION, item.id)]
    assert summaries == ["title, why", "brief"]


def test_audit_is_scoped_to_the_session(store: SqlAlchemyWorkTreeStore) -> None:
    """One session's audit trail never contains another's entries."""
    mine = store.create(SESSION, title="Mine")
    store.create(OTHER_SESSION, title="Theirs")
    assert [e.item_id for e in store.list_events(SESSION)] == [mine.id]


def test_deleting_an_item_retains_no_text_the_user_typed(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """Every user-entered value is gone from the trail once the item is deleted.

    The audit table outlives the rows it describes, so anything it copies out of
    a work item survives the delete that was meant to remove it. A single
    sentinel written into every free-text field proves no path — create, update,
    status, reorder, defer, resume or delete — smuggles a value across.
    """
    sentinel = "quokka-ledger-sentinel-do-not-retain"
    item = store.create(
        SESSION,
        title=f"title {sentinel}",
        brief=f"brief {sentinel}",
        why=f"why {sentinel}",
        next_action=f"next {sentinel}",
        evidence=f"evidence {sentinel}",
        source_ref=f"ref {sentinel}",
        actor="alice@example.com",
    )
    other = store.create(SESSION, title="Sibling")
    item = store.update(
        SESSION,
        item.id,
        expected_version=item.version,
        title=f"renamed {sentinel}",
        brief=f"rebriefed {sentinel}",
        sort_index=1,
    )
    item = store.update(SESSION, item.id, expected_version=item.version, status="done")
    item = store.reorder(SESSION, item.id, new_index=0, expected_version=item.version)
    item = store.defer(SESSION, item.id, expected_version=item.version)
    item = store.resume(SESSION, item.id, expected_version=item.version)
    store.delete(SESSION, item.id, expected_version=item.version)

    assert store.get(SESSION, item.id) is None
    events = store.list_events(SESSION)
    # Both items' events, including the sibling's reorder entries.
    assert [e.item_id for e in events if e.item_id == item.id]
    for event in events:
        assert sentinel not in repr(vars(event)), f"{event.action} retained user text"
    # The delete stays legible without the title it removed.
    assert [e.action for e in store.list_events(SESSION, item.id)][-1] == "deleted"
    assert other.id != item.id


# ── one user action is one transaction ────────────────────────────────────


def test_fields_and_sort_index_apply_as_one_versioned_change(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """Renaming and moving in one call bumps the version once and audits once.

    The route sends both halves of a single drag-and-rename together, so the
    store has to treat them as one mutation — otherwise the caller cannot tell
    which version to hold, and two events describe one user action.
    """
    first = store.create(SESSION, title="First")
    second = store.create(SESSION, title="Second")

    moved = store.update(
        SESSION,
        second.id,
        expected_version=second.version,
        title="Renamed",
        sort_index=0,
        actor="alice@example.com",
    )
    assert moved.title == "Renamed"
    assert moved.version == second.version + 1
    assert [i.title for i in store.list_tree(SESSION)] == ["Renamed", "First"]

    actions = [e.action for e in store.list_events(SESSION, second.id)]
    assert actions == ["created", "updated"]
    assert store.list_events(SESSION, second.id)[-1].summary == "sort_index, title"
    assert store.get(SESSION, first.id).sort_order == 1


def test_a_stale_combined_change_applies_nothing(store: SqlAlchemyWorkTreeStore) -> None:
    """A conflicting combined mutation leaves the item exactly as it was.

    This is the half-applied write the split update-then-reorder path allowed:
    the rename committed, the move hit a 409, and the caller was told the whole
    request failed while other windows never heard about the rename at all.
    """
    first = store.create(SESSION, title="First")
    second = store.create(SESSION, title="Second")
    stale_version = second.version

    # Another window gets there first, so the caller's version is behind.
    store.update(SESSION, second.id, expected_version=second.version, brief="Someone else")

    with pytest.raises(OmnigentError) as excinfo:
        store.update(
            SESSION,
            second.id,
            expected_version=stale_version,
            title="Renamed",
            sort_index=0,
        )
    assert excinfo.value.code == ErrorCode.CONFLICT

    after = store.get(SESSION, second.id)
    assert after.title == "Second"
    assert [i.id for i in store.list_tree(SESSION)] == [first.id, second.id]
    assert [e.action for e in store.list_events(SESSION, second.id)] == ["created", "updated"]


def test_update_needs_something_to_change(store: SqlAlchemyWorkTreeStore) -> None:
    """A call with no field and no ``sort_index`` is a caller bug, not a no-op."""
    item = store.create(SESSION, title="Task")
    with pytest.raises(OmnigentError) as excinfo:
        store.update(SESSION, item.id, expected_version=item.version)
    assert excinfo.value.code == ErrorCode.INVALID_INPUT


def test_a_moved_sibling_is_versioned_and_audited(store: SqlAlchemyWorkTreeStore) -> None:
    """Siblings whose displayed order changed are stale for anyone holding them.

    Order is part of what a client reads, so a sibling that moved has to bump
    its version — otherwise a window editing from a cached copy writes over the
    new order and optimistic concurrency never notices.
    """
    first = store.create(SESSION, title="First")
    second = store.create(SESSION, title="Second")
    third = store.create(SESSION, title="Third")

    store.reorder(SESSION, third.id, new_index=0, expected_version=third.version)

    after = {i.id: i for i in store.list_tree(SESSION)}
    assert after[first.id].version == first.version + 1
    assert after[second.id].version == second.version + 1
    assert after[third.id].version == third.version + 1
    assert [e.action for e in store.list_events(SESSION, first.id)] == ["created", "reordered"]

    # And the bump is enforced: the pre-move version no longer writes.
    with pytest.raises(OmnigentError) as excinfo:
        store.update(SESSION, first.id, expected_version=first.version, title="Stale write")
    assert excinfo.value.code == ErrorCode.CONFLICT


def test_a_sibling_that_did_not_move_is_left_alone(store: SqlAlchemyWorkTreeStore) -> None:
    """Only genuinely moved siblings are versioned, so the trail stays honest."""
    first = store.create(SESSION, title="First")
    second = store.create(SESSION, title="Second")
    third = store.create(SESSION, title="Third")

    store.reorder(SESSION, third.id, new_index=1, expected_version=third.version)

    after = {i.id: i for i in store.list_tree(SESSION)}
    assert after[first.id].version == first.version
    assert after[second.id].version == second.version + 1
    assert [e.action for e in store.list_events(SESSION, first.id)] == ["created"]


# ── audit sequence allocation ─────────────────────────────────────────────


def _audit_seqs(db_uri: str, conversation_id: str = SESSION) -> list[int]:
    """Raw ``seq`` values for a session's audit trail, oldest first.

    ``seq`` is an internal ordering key rather than part of
    :class:`~omnigent.entities.work_item.WorkItemEvent`, so these tests read the
    column directly instead of widening the entity for their own convenience.

    :param db_uri: The database the store is writing to.
    :param conversation_id: Session whose trail to read.
    :returns: The stored sequence numbers, ascending.
    """
    engine = sa.create_engine(db_uri)
    try:
        with engine.connect() as conn:
            return list(
                conn.execute(
                    sa.text(
                        "SELECT seq FROM work_item_events "
                        "WHERE workspace_id = 0 AND conversation_id = :conv "
                        "ORDER BY seq"
                    ),
                    {"conv": uuid.UUID(conversation_id).bytes},
                )
                .scalars()
                .all()
            )
    finally:
        engine.dispose()


def test_duplicate_audit_sequences_are_refused_by_the_database(
    store: SqlAlchemyWorkTreeStore, db_uri: str
) -> None:
    """``seq`` is unique per session in the schema, not just by convention.

    Without the constraint two concurrent mutations both read the same
    high-water mark and both write it, and the trail can no longer say which
    change happened first — the exact ambiguity ``seq`` exists to remove.
    """
    item = store.create(SESSION, title="Task")
    [taken] = _audit_seqs(db_uri)

    engine = sa.create_engine(db_uri)
    try:
        with engine.begin() as conn, pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO work_item_events "
                    "(workspace_id, id, conversation_id, item_id, action, actor, "
                    " version, summary, created_at, seq) "
                    "VALUES (0, :id, :conv, :item, 'created', NULL, 1, NULL, 1, :seq)"
                ),
                {
                    "id": uuid.uuid4().bytes,
                    "conv": uuid.UUID(SESSION).bytes,
                    "item": uuid.UUID(item.id).bytes,
                    "seq": taken,
                },
            )
    finally:
        engine.dispose()


def test_a_lost_sequence_race_retries_instead_of_failing(
    store: SqlAlchemyWorkTreeStore, db_uri: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing the race for a sequence number costs a retry, not the write.

    Simulates the interleaving directly: the first allocation hands back a
    number another transaction already committed. The unique index refuses it,
    the savepoint rolls back, and the re-read finds the real mark.
    """
    item = store.create(SESSION, title="Task")
    [taken] = _audit_seqs(db_uri)

    original = SqlAlchemyWorkTreeStore._next_seq
    calls: list[int] = []

    def _stale_once(self, session, conversation_id):  # type: ignore[no-untyped-def]
        calls.append(1)
        if len(calls) == 1:
            return taken
        return original(self, session, conversation_id)

    monkeypatch.setattr(SqlAlchemyWorkTreeStore, "_next_seq", _stale_once)
    store.update(SESSION, item.id, expected_version=item.version, status="working")

    seqs = _audit_seqs(db_uri)
    assert len(calls) == 2
    assert [e.action for e in store.list_events(SESSION, item.id)] == [
        "created",
        "status_changed",
    ]
    assert seqs == [taken, taken + 1]
    assert store.get(SESSION, item.id).status == "working"


def test_concurrent_mutations_never_share_a_sequence_number(db_uri: str) -> None:
    """Whatever survives real concurrency has a unique place in the trail.

    Real threads against whichever database the suite is configured for,
    because the race is between connections: the unique index and the retry
    that turns its error back into progress behave differently per server, so
    pinning this to SQLite would leave the Postgres and MySQL paths unproven.
    Some writers may lose to the database's own locking; the invariant is that
    no two entries that *did* land claim the same position.
    """
    uri = db_uri
    store = SqlAlchemyWorkTreeStore(uri)
    items = [store.create(SESSION, title=f"Task {n}") for n in range(8)]

    barrier = threading.Barrier(len(items))
    failures: list[Exception] = []

    def _mutate(item: WorkItem) -> None:
        barrier.wait(timeout=10)
        try:
            store.update(SESSION, item.id, expected_version=item.version, status="working")
        except Exception as exc:
            failures.append(exc)

    threads = [threading.Thread(target=_mutate, args=(item,)) for item in items]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    seqs = _audit_seqs(uri)
    assert len(set(seqs)) == len(seqs), f"duplicate audit seq under concurrency: {seqs}"
    # Not every writer has to win, but the trail must account for those that did.
    assert len(seqs) == len(items) + (len(items) - len(failures))


# ── related projects ──────────────────────────────────────────────────────


def test_related_projects_are_additive_and_idempotent(
    store: SqlAlchemyWorkTreeStore,
) -> None:
    """A session can relate to zero or more projects without being duplicated."""
    assert store.list_related_projects(SESSION) == []
    assert store.add_related_project(SESSION, PROJECT) is True
    assert store.add_related_project(SESSION, PROJECT) is False
    assert store.list_related_projects(SESSION) == [PROJECT]


def test_removing_a_related_project_is_idempotent(store: SqlAlchemyWorkTreeStore) -> None:
    """Unrelating a project twice is safe."""
    store.add_related_project(SESSION, PROJECT)
    assert store.remove_related_project(SESSION, PROJECT) is True
    assert store.remove_related_project(SESSION, PROJECT) is False
    assert store.list_related_projects(SESSION) == []


def test_related_projects_are_scoped_per_session(store: SqlAlchemyWorkTreeStore) -> None:
    """Relating a project to one session does not relate it to another."""
    store.add_related_project(SESSION, PROJECT)
    assert store.list_related_projects(OTHER_SESSION) == []


def test_item_project_assignment_is_optional(store: SqlAlchemyWorkTreeStore) -> None:
    """An item carries a project only when it differs from the session's home."""
    default = store.create(SESSION, title="Same project as the session")
    assigned = store.create(SESSION, title="Elsewhere", project_id=PROJECT)
    assert default.project_id is None
    assert assigned.project_id == PROJECT
