"""SQLAlchemy-backed work tree store."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import asc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlSessionRelatedProject,
    SqlWorkItem,
    SqlWorkItemEvent,
    current_workspace_id,
)
from omnigent.db.enum_codecs import (
    decode_work_item_delivery_state,
    decode_work_item_discovery_class,
    decode_work_item_source_kind,
    decode_work_item_status,
    encode_work_item_delivery_state,
    encode_work_item_discovery_class,
    encode_work_item_source_kind,
    encode_work_item_status,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
)
from omnigent.entities.work_item import MAX_WORK_ITEM_DEPTH, WorkItem, WorkItemEvent
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.work_tree_store import WorkTreeStore

# Longest title we persist. Titles are the one line always on screen, so a
# runaway provider string is truncated at the boundary rather than rejected —
# losing the tail of a title is far better than losing the observation.
_TITLE_MAX_LEN = 512

# Longest free-text field (brief / why / next_action / evidence). Generous
# enough for real context, tight enough that a pasted log cannot bloat the row.
_TEXT_MAX_LEN = 4096

# How many times a mutation re-reads the audit high-water mark when another
# transaction took the sequence number first. The unique index makes the clash
# an error rather than a duplicate; this turns that error back into progress.
_SEQ_ALLOCATION_ATTEMPTS = 5

# Fields ``update`` accepts. Anything else is a caller bug, not a silent no-op.
_UPDATABLE_FIELDS = frozenset(
    {
        "title",
        "brief",
        "why",
        "next_action",
        "status",
        "delivery_state",
        "project_id",
        "discovery_class",
        "evidence",
        "collapsed",
        "source_ref",
    }
)


def _clean_title(title: str) -> str:
    """Trim and bound a title, refusing an empty one.

    :param title: Raw title text.
    :returns: The trimmed, length-bounded title.
    :raises OmnigentError: ``INVALID_INPUT`` when the title is blank.
    """
    cleaned = (title or "").strip()
    if not cleaned:
        raise OmnigentError("work item title must not be empty", code=ErrorCode.INVALID_INPUT)
    return cleaned[:_TITLE_MAX_LEN]


def _clean_text(value: str | None) -> str | None:
    """Trim and bound an optional free-text field; blank becomes ``None``."""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned[:_TEXT_MAX_LEN] if cleaned else None


def _to_entity(row: SqlWorkItem) -> WorkItem:
    """
    Convert a :class:`SqlWorkItem` ORM row to a :class:`WorkItem`.

    :param row: The SQLAlchemy ORM row to convert.
    :returns: A :class:`WorkItem` dataclass instance.
    """
    return WorkItem(
        id=row.id,
        conversation_id=row.conversation_id,
        parent_id=row.parent_id,
        depth=row.depth,
        title=row.title,
        brief=row.brief,
        why=row.why,
        next_action=row.next_action,
        status=decode_work_item_status(row.status),
        delivery_state=(
            decode_work_item_delivery_state(row.delivery_state)
            if row.delivery_state is not None
            else None
        ),
        project_id=row.project_id,
        source_kind=decode_work_item_source_kind(row.source_kind),
        source_ref=row.source_ref,
        discovery_class=(
            decode_work_item_discovery_class(row.discovery_class)
            if row.discovery_class is not None
            else None
        ),
        evidence=row.evidence,
        sort_order=row.sort_order,
        collapsed=row.collapsed,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
        deferred_at=row.deferred_at,
    )


def _event_to_entity(row: SqlWorkItemEvent) -> WorkItemEvent:
    """Convert a :class:`SqlWorkItemEvent` ORM row to a :class:`WorkItemEvent`."""
    return WorkItemEvent(
        id=row.id,
        conversation_id=row.conversation_id,
        item_id=row.item_id,
        action=row.action,
        actor=row.actor,
        version=row.version,
        summary=row.summary,
        created_at=row.created_at,
    )


def _not_found(item_id: str) -> OmnigentError:
    """Build the canonical "no such item in this session" error."""
    return OmnigentError(f"work item {item_id!r} not found", code=ErrorCode.NOT_FOUND)


def _stale(item_id: str, expected: int, actual: int) -> OmnigentError:
    """Build the canonical optimistic-concurrency conflict error."""
    return OmnigentError(
        f"work item {item_id!r} changed since version {expected} (now {actual})",
        code=ErrorCode.CONFLICT,
    )


class SqlAlchemyWorkTreeStore(WorkTreeStore):
    """
    SQLAlchemy-backed implementation of :class:`WorkTreeStore`.

    Every query is scoped by ``workspace_id`` (tenant partition) and
    ``conversation_id`` (a tree belongs to one session). The product rules that
    keep the tree trustworthy — depth cap, same-session parentage, version
    checks, no cascading delete — are enforced here so no caller can skip them.
    """

    def __init__(self, storage_location: str) -> None:
        """
        Initialize the SQLAlchemy work tree store.

        :param storage_location: SQLAlchemy database URI,
            e.g. ``"sqlite:///chat.db"``.
        """
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.work_tree_store",
        )

    # ── internals ──────────────────────────────────────

    def _row(self, session: Session, conversation_id: str, item_id: str) -> SqlWorkItem | None:
        """Load one row, treating an item from another session as absent."""
        row = session.get(SqlWorkItem, (current_workspace_id(), item_id))
        if row is None or row.conversation_id != conversation_id:
            return None
        return row

    def _next_seq(self, session: Session, conversation_id: str) -> int:
        """Return the next per-session audit sequence number.

        The trail is human-scale (one entry per deliberate edit), so reading the
        session's current high-water mark per append is cheap and keeps the
        order exact even for several edits inside the same second.
        """
        high_water = session.execute(
            select(func.max(SqlWorkItemEvent.seq)).where(
                SqlWorkItemEvent.workspace_id == current_workspace_id(),
                SqlWorkItemEvent.conversation_id == conversation_id,
            )
        ).scalar()
        return (high_water or 0) + 1

    def _audit(
        self,
        session: Session,
        row: SqlWorkItem,
        *,
        action: str,
        actor: str | None,
        summary: str | None = None,
    ) -> None:
        """Append one sanitized audit entry for ``row``'s current version.

        ``summary`` carries structure only — the names of the fields touched, or
        a position. Never a field value: entries outlive the row, so a summary
        that echoed the title would retain it past the item's deletion.

        Read-then-write on the high-water mark races when two mutations touch
        one session at once, so ``uq_work_item_events_conversation_seq`` makes a
        duplicate an error the database refuses. Each attempt writes inside a
        savepoint: a clash rolls that savepoint back, leaving the caller's
        transaction intact, and the next attempt re-reads the mark the winner
        just committed.
        """
        for _ in range(_SEQ_ALLOCATION_ATTEMPTS):
            seq = self._next_seq(session, row.conversation_id)
            try:
                with session.begin_nested():
                    session.add(
                        SqlWorkItemEvent(
                            id=uuid.uuid4().hex,
                            conversation_id=row.conversation_id,
                            item_id=row.id,
                            action=action,
                            actor=actor[:128] if actor else None,
                            version=row.version,
                            summary=summary[:512] if summary else None,
                            created_at=now_epoch(),
                            seq=seq,
                        )
                    )
                    session.flush()
            except IntegrityError:
                continue
            session.flush()
            return
        raise OmnigentError(
            f"could not allocate an audit sequence for session {row.conversation_id!r}; "
            "too many concurrent changes",
            code=ErrorCode.CONFLICT,
        )

    def _next_sort_order(
        self, session: Session, conversation_id: str, parent_id: str | None
    ) -> int:
        """Return the sort order that appends after the existing siblings."""
        stmt = select(SqlWorkItem.sort_order).where(
            SqlWorkItem.workspace_id == current_workspace_id(),
            SqlWorkItem.conversation_id == conversation_id,
            SqlWorkItem.parent_id.is_(None)
            if parent_id is None
            else SqlWorkItem.parent_id == parent_id,
        )
        orders = list(session.execute(stmt).scalars().all())
        return (max(orders) + 1) if orders else 0

    def _siblings(
        self, session: Session, conversation_id: str, parent_id: str | None
    ) -> list[SqlWorkItem]:
        """Return the rows sharing ``parent_id``, in current display order."""
        stmt = (
            select(SqlWorkItem)
            .where(
                SqlWorkItem.workspace_id == current_workspace_id(),
                SqlWorkItem.conversation_id == conversation_id,
                SqlWorkItem.parent_id.is_(None)
                if parent_id is None
                else SqlWorkItem.parent_id == parent_id,
            )
            .order_by(asc(SqlWorkItem.sort_order), asc(SqlWorkItem.id))
        )
        return list(session.execute(stmt).scalars().all())

    def _apply_sort_index(
        self,
        session: Session,
        row: SqlWorkItem,
        new_index: int,
        *,
        actor: str | None,
    ) -> int:
        """Renumber ``row``'s sibling group so ``row`` lands at ``new_index``.

        Every sibling whose displayed position actually moved is versioned and
        audited like any other mutation: a client holding the old version is
        genuinely stale, and optimistic concurrency has to be able to say so.

        :returns: The clamped index ``row`` was placed at.
        """
        siblings = self._siblings(session, row.conversation_id, row.parent_id)
        others = [s for s in siblings if s.id != row.id]
        index = max(0, min(new_index, len(others)))
        for position, sibling in enumerate([*others[:index], row, *others[index:]]):
            if sibling.sort_order == position:
                continue
            sibling.sort_order = position
            if sibling.id == row.id:
                # The moved row is versioned and audited by the caller, which
                # folds the move into that request's single entry.
                continue
            self._touch(sibling)
            self._audit(
                session,
                sibling,
                action="reordered",
                actor=actor,
                summary=f"position {position}",
            )
        return index

    def _check_version(self, row: SqlWorkItem, expected_version: int) -> None:
        """Refuse a write whose caller was looking at an older version."""
        if row.version != expected_version:
            raise _stale(row.id, expected_version, row.version)

    def _touch(self, row: SqlWorkItem) -> None:
        """Bump the version and update stamp after a successful mutation."""
        row.version += 1
        row.updated_at = now_epoch()

    # ── reads ──────────────────────────────────────────

    def list_tree(self, conversation_id: str) -> list[WorkItem]:
        """Return every item in a session's tree, parents before their children."""
        with self._session("list_work_items") as session:
            stmt = (
                select(SqlWorkItem)
                .where(
                    SqlWorkItem.workspace_id == current_workspace_id(),
                    SqlWorkItem.conversation_id == conversation_id,
                )
                .order_by(asc(SqlWorkItem.sort_order), asc(SqlWorkItem.id))
            )
            rows = [_to_entity(r) for r in session.execute(stmt).scalars().all()]

        by_parent: dict[str | None, list[WorkItem]] = {}
        for item in rows:
            by_parent.setdefault(item.parent_id, []).append(item)

        ordered: list[WorkItem] = []

        def _walk(parent_id: str | None) -> None:
            for item in by_parent.get(parent_id, []):
                ordered.append(item)
                _walk(item.id)

        _walk(None)
        # An item whose parent row vanished would otherwise be silently dropped;
        # surface it at the end rather than losing a user's context.
        seen = {item.id for item in ordered}
        ordered.extend(item for item in rows if item.id not in seen)
        return ordered

    def get(self, conversation_id: str, item_id: str) -> WorkItem | None:
        """Return one item, or ``None`` when it does not exist in this session."""
        with self._session("select_work_item") as session:
            row = self._row(session, conversation_id, item_id)
            return _to_entity(row) if row is not None else None

    def list_events(self, conversation_id: str, item_id: str | None = None) -> list[WorkItemEvent]:
        """Return the sanitized audit trail, oldest first."""
        with self._session("list_work_item_events") as session:
            stmt = (
                select(SqlWorkItemEvent)
                .where(
                    SqlWorkItemEvent.workspace_id == current_workspace_id(),
                    SqlWorkItemEvent.conversation_id == conversation_id,
                )
                .order_by(asc(SqlWorkItemEvent.seq), asc(SqlWorkItemEvent.id))
            )
            if item_id is not None:
                stmt = stmt.where(SqlWorkItemEvent.item_id == item_id)
            return [_event_to_entity(r) for r in session.execute(stmt).scalars().all()]

    # ── writes ─────────────────────────────────────────

    def create(
        self,
        conversation_id: str,
        *,
        title: str,
        parent_id: str | None = None,
        brief: str | None = None,
        why: str | None = None,
        next_action: str | None = None,
        status: str = "not_started",
        delivery_state: str | None = None,
        project_id: str | None = None,
        source_kind: str = "user",
        source_ref: str | None = None,
        discovery_class: str | None = None,
        evidence: str | None = None,
        actor: str | None = None,
    ) -> WorkItem:
        """Insert a new item, appended after its existing siblings."""
        clean_title = _clean_title(title)
        with self._session("insert_work_item") as session:
            depth = 1
            if parent_id is not None:
                parent = self._row(session, conversation_id, parent_id)
                if parent is None:
                    raise _not_found(parent_id)
                depth = parent.depth + 1
                if depth > MAX_WORK_ITEM_DEPTH:
                    raise OmnigentError(
                        f"work tree is limited to {MAX_WORK_ITEM_DEPTH} levels; "
                        f"{parent_id!r} is already at the deepest level",
                        code=ErrorCode.INVALID_INPUT,
                    )
            row = SqlWorkItem(
                id=uuid.uuid4().hex,
                conversation_id=conversation_id,
                parent_id=parent_id,
                depth=depth,
                title=clean_title,
                brief=_clean_text(brief),
                why=_clean_text(why),
                next_action=_clean_text(next_action),
                evidence=_clean_text(evidence),
                status=encode_work_item_status(status),
                delivery_state=(
                    encode_work_item_delivery_state(delivery_state)
                    if delivery_state is not None
                    else None
                ),
                source_kind=encode_work_item_source_kind(source_kind),
                discovery_class=(
                    encode_work_item_discovery_class(discovery_class)
                    if discovery_class is not None
                    else None
                ),
                project_id=project_id,
                source_ref=source_ref[:256] if source_ref else None,
                sort_order=self._next_sort_order(session, conversation_id, parent_id),
                collapsed=False,
                version=1,
                created_at=now_epoch(),
                updated_at=None,
                completed_at=now_epoch() if status == "done" else None,
                deferred_at=None,
            )
            session.add(row)
            session.flush()
            # Field names, never their values — the trail outlives the row, so a
            # summary echoing the title would keep it after the item is deleted.
            populated = sorted(
                name
                for name, value in (
                    ("title", clean_title),
                    ("brief", row.brief),
                    ("why", row.why),
                    ("next_action", row.next_action),
                    ("evidence", row.evidence),
                    ("parent_id", parent_id),
                    ("project_id", project_id),
                    ("source_ref", row.source_ref),
                    ("delivery_state", delivery_state),
                    ("discovery_class", discovery_class),
                )
                if value is not None
            )
            self._audit(session, row, action="created", actor=actor, summary=", ".join(populated))
            return _to_entity(row)

    def update(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
        sort_index: int | None = None,
        **fields: Any,
    ) -> WorkItem:
        """Apply a partial update, and any reorder, as one transaction.

        Field edits and ``sort_index`` arrive together from a single user
        action, so they commit together, bump the version once, and produce one
        audit entry. Splitting them let the first half commit while the second
        hit a conflict, leaving a change nobody was told about.
        """
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise OmnigentError(
                f"unknown work item field(s): {', '.join(sorted(unknown))}",
                code=ErrorCode.INVALID_INPUT,
            )
        if not fields and sort_index is None:
            raise OmnigentError(
                "update needs at least one field or a sort_index",
                code=ErrorCode.INVALID_INPUT,
            )
        with self._session("update_work_item") as session:
            row = self._row(session, conversation_id, item_id)
            if row is None:
                raise _not_found(item_id)
            self._check_version(row, expected_version)

            if "title" in fields:
                row.title = _clean_title(fields["title"])
            for name in ("brief", "why", "next_action", "evidence"):
                if name in fields:
                    setattr(row, name, _clean_text(fields[name]))
            if "status" in fields:
                row.status = encode_work_item_status(fields["status"])
                # completed_at tracks the work axis only; reopening an item
                # clears it so a stale stamp never implies a finished branch.
                row.completed_at = now_epoch() if fields["status"] == "done" else None
            if "delivery_state" in fields:
                value = fields["delivery_state"]
                row.delivery_state = (
                    encode_work_item_delivery_state(value) if value is not None else None
                )
            if "discovery_class" in fields:
                value = fields["discovery_class"]
                row.discovery_class = (
                    encode_work_item_discovery_class(value) if value is not None else None
                )
            if "project_id" in fields:
                row.project_id = fields["project_id"]
            if "source_ref" in fields:
                value = fields["source_ref"]
                row.source_ref = (value or None) and str(value)[:256]
            if "collapsed" in fields:
                row.collapsed = bool(fields["collapsed"])

            changed = set(fields)
            index: int | None = None
            if sort_index is not None:
                index = self._apply_sort_index(session, row, sort_index, actor=actor)
                changed.add("sort_index")

            self._touch(row)
            if fields:
                action = "status_changed" if changed == {"status"} else "updated"
                summary = ", ".join(sorted(changed))
            else:
                # A reorder-only request; the guard above rules out neither.
                action = "reordered"
                summary = f"position {index}"
            self._audit(session, row, action=action, actor=actor, summary=summary)
            return _to_entity(row)

    def reorder(
        self,
        conversation_id: str,
        item_id: str,
        *,
        new_index: int,
        expected_version: int,
        actor: str | None = None,
    ) -> WorkItem:
        """Move an item among its siblings. Only siblings are renumbered."""
        with self._session("reorder_work_item") as session:
            row = self._row(session, conversation_id, item_id)
            if row is None:
                raise _not_found(item_id)
            self._check_version(row, expected_version)
            index = self._apply_sort_index(session, row, new_index, actor=actor)
            self._touch(row)
            self._audit(session, row, action="reordered", actor=actor, summary=f"position {index}")
            return _to_entity(row)

    def delete(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
    ) -> None:
        """Delete a childless item. Never cascades."""
        with self._session("delete_work_item") as session:
            row = self._row(session, conversation_id, item_id)
            if row is None:
                raise _not_found(item_id)
            self._check_version(row, expected_version)
            child = session.execute(
                select(SqlWorkItem.id).where(
                    SqlWorkItem.workspace_id == current_workspace_id(),
                    SqlWorkItem.conversation_id == conversation_id,
                    SqlWorkItem.parent_id == item_id,
                )
            ).first()
            if child is not None:
                raise OmnigentError(
                    f"work item {item_id!r} still has children; move or delete them first",
                    code=ErrorCode.CONFLICT,
                )
            # Audit before the delete so the trail outlives the row, at the
            # version the item ceased to exist at. The action, item id, actor and
            # version say what happened; the title is exactly what the delete was
            # asked to remove, so it is not copied into the surviving entry.
            row.version += 1
            self._audit(session, row, action="deleted", actor=actor)
            session.delete(row)

    def defer(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
    ) -> WorkItem:
        """Defer an item: status ``paused`` and a ``deferred_at`` stamp, together."""
        with self._session("defer_work_item") as session:
            row = self._row(session, conversation_id, item_id)
            if row is None:
                raise _not_found(item_id)
            self._check_version(row, expected_version)
            row.status = encode_work_item_status("paused")
            row.deferred_at = now_epoch()
            self._touch(row)
            self._audit(session, row, action="deferred", actor=actor)
            return _to_entity(row)

    def resume(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
    ) -> WorkItem:
        """Resume a deferred item: status ``working`` and ``deferred_at`` cleared."""
        with self._session("resume_work_item") as session:
            row = self._row(session, conversation_id, item_id)
            if row is None:
                raise _not_found(item_id)
            self._check_version(row, expected_version)
            row.status = encode_work_item_status("working")
            row.deferred_at = None
            self._touch(row)
            self._audit(session, row, action="resumed", actor=actor)
            return _to_entity(row)

    # ── related projects ───────────────────────────────

    def list_related_projects(self, conversation_id: str) -> list[str]:
        """Return the session's related project ids (its home project excluded)."""
        with self._session("list_session_related_projects") as session:
            stmt = (
                select(SqlSessionRelatedProject.project_id)
                .where(
                    SqlSessionRelatedProject.workspace_id == current_workspace_id(),
                    SqlSessionRelatedProject.conversation_id == conversation_id,
                )
                .order_by(
                    asc(SqlSessionRelatedProject.created_at),
                    asc(SqlSessionRelatedProject.project_id),
                )
            )
            return list(session.execute(stmt).scalars().all())

    def add_related_project(self, conversation_id: str, project_id: str) -> bool:
        """Relate a project to this session. Idempotent."""
        with self._session("insert_session_related_project") as session:
            existing = session.get(
                SqlSessionRelatedProject,
                (current_workspace_id(), conversation_id, project_id),
            )
            if existing is not None:
                return False
            session.add(
                SqlSessionRelatedProject(
                    conversation_id=conversation_id,
                    project_id=project_id,
                    created_at=now_epoch(),
                )
            )
            return True

    def remove_related_project(self, conversation_id: str, project_id: str) -> bool:
        """Unrelate a project from this session. Idempotent."""
        with self._session("delete_session_related_project") as session:
            row = session.get(
                SqlSessionRelatedProject,
                (current_workspace_id(), conversation_id, project_id),
            )
            if row is None:
                return False
            session.delete(row)
            return True
