"""Work tree store — persists a session's durable, provider-neutral Work Tree.

This store owns the ``work_items``, ``work_item_events`` and
``session_related_projects`` tables. Every method is scoped by
``conversation_id``: a Work Tree belongs to exactly one session, and a parent
and child always live in the same one.

The store is the layer that enforces the tree's product rules — maximum depth,
same-session parentage, optimistic versioning, and refusing to cascade a delete
— so no route, adapter, or provider observation can bypass them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from omnigent.entities.work_item import WorkItem, WorkItemEvent


class WorkTreeStore(ABC):
    """
    Abstract base for Work Tree persistence.

    All reads and writes are scoped by ``conversation_id``; callers are expected
    to have already authorized access to that session.
    """

    def __init__(self, storage_location: str) -> None:
        """
        Initialize the work tree store.

        :param storage_location: Backend-specific storage URI,
            e.g. ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    @abstractmethod
    def list_tree(self, conversation_id: str) -> list[WorkItem]:
        """
        Return every item in a session's tree, parents before their children.

        A session that has never had an item returns ``[]`` — that is the
        migrated state of every pre-existing session, not an error.

        :param conversation_id: Owning session.
        :returns: Items in depth-first order, siblings by ``sort_order``.
        """
        ...

    @abstractmethod
    def get(self, conversation_id: str, item_id: str) -> WorkItem | None:
        """
        Return one item, or ``None`` when it does not exist in this session.

        :param conversation_id: Owning session.
        :param item_id: Opaque work item identifier.
        :returns: The :class:`WorkItem`, or ``None``.
        """
        ...

    @abstractmethod
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
        """
        Insert a new item, appended after its existing siblings.

        :param conversation_id: Owning session.
        :param title: Short plain-language name; trimmed, non-empty.
        :param parent_id: Parent item, or ``None`` for a root item.
        :param brief: Optional one-sentence expansion of the title.
        :param why: Optional reason this matters.
        :param next_action: Optional concrete next step.
        :param status: Initial work status; see ``WORK_STATUSES``.
        :param delivery_state: Optional initial delivery state.
        :param project_id: Project when it differs from the session's home
            project; ``None`` means "same as the session".
        :param source_kind: Where the item came from; see ``SOURCE_KINDS``.
        :param source_ref: Safe provider/orchestrator handle, never a payload.
        :param discovery_class: Optional relation to the work that found it.
        :param evidence: Optional human-readable evidence summary.
        :param actor: Audit actor recorded for the ``created`` event.
        :returns: The newly created :class:`WorkItem`.
        :raises OmnigentError: ``INVALID_INPUT`` for an empty title, an unknown
            enum value, or a parent that would create a fourth level;
            ``NOT_FOUND`` when ``parent_id`` is not an item of this session.
        """
        ...

    @abstractmethod
    def update(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
        sort_index: int | None = None,
        **fields: object,
    ) -> WorkItem:
        """
        Apply a partial update, and any reorder, as one transaction.

        Only the fields present in ``fields`` change; a field explicitly set to
        ``None`` clears it. ``status`` and ``delivery_state`` move on their own
        axes — setting one never implies the other.

        Field edits and ``sort_index`` come from one user action, so they apply
        together or not at all, bump the version once, and produce one audit
        entry. Callers therefore get exactly one state to broadcast.

        :param conversation_id: Owning session.
        :param item_id: Opaque work item identifier.
        :param expected_version: The version the caller last saw.
        :param actor: Audit actor recorded for the event.
        :param sort_index: New 0-based position among the item's siblings, or
            ``None`` to leave the order alone; clamped into range.
        :param fields: Mutable field values to apply.
        :returns: The updated :class:`WorkItem`, with a bumped version.
        :raises OmnigentError: ``NOT_FOUND`` when the item is not in this
            session; ``CONFLICT`` when ``expected_version`` is stale;
            ``INVALID_INPUT`` for an unknown field, an unknown enum value, or a
            call carrying neither a field nor a ``sort_index``.
        """
        ...

    @abstractmethod
    def reorder(
        self,
        conversation_id: str,
        item_id: str,
        *,
        new_index: int,
        expected_version: int,
        actor: str | None = None,
    ) -> WorkItem:
        """
        Move an item among its siblings. Only siblings are renumbered.

        Every sibling that actually moves is versioned and audited too, so
        ``sort_order`` sits inside the optimistic-concurrency contract rather
        than beside it: a client holding a pre-move copy of a sibling is stale
        and its next write is refused.

        :param conversation_id: Owning session.
        :param item_id: The item to move.
        :param new_index: Target 0-based position among its siblings; clamped
            into range.
        :param expected_version: The version the caller last saw.
        :param actor: Audit actor recorded for the event.
        :returns: The moved :class:`WorkItem`.
        :raises OmnigentError: ``NOT_FOUND`` / ``CONFLICT`` as for
            :meth:`update`.
        """
        ...

    @abstractmethod
    def delete(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
    ) -> None:
        """
        Delete a childless item. Never cascades.

        :param conversation_id: Owning session.
        :param item_id: The item to delete.
        :param expected_version: The version the caller last saw.
        :param actor: Audit actor recorded for the event.
        :raises OmnigentError: ``NOT_FOUND`` when the item is not in this
            session; ``CONFLICT`` when the version is stale or the item still
            has children — the caller must deal with the children first.
        """
        ...

    @abstractmethod
    def defer(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
    ) -> WorkItem:
        """
        Defer an item: status ``paused`` and a ``deferred_at`` stamp, together.

        :param conversation_id: Owning session.
        :param item_id: The item to defer.
        :param expected_version: The version the caller last saw.
        :param actor: Audit actor recorded for the event.
        :returns: The deferred :class:`WorkItem`.
        :raises OmnigentError: ``NOT_FOUND`` / ``CONFLICT`` as for
            :meth:`update`.
        """
        ...

    @abstractmethod
    def resume(
        self,
        conversation_id: str,
        item_id: str,
        *,
        expected_version: int,
        actor: str | None = None,
    ) -> WorkItem:
        """
        Resume a deferred item: status ``working`` and ``deferred_at`` cleared.

        :param conversation_id: Owning session.
        :param item_id: The item to resume.
        :param expected_version: The version the caller last saw.
        :param actor: Audit actor recorded for the event.
        :returns: The resumed :class:`WorkItem`.
        :raises OmnigentError: ``NOT_FOUND`` / ``CONFLICT`` as for
            :meth:`update`.
        """
        ...

    @abstractmethod
    def list_events(self, conversation_id: str, item_id: str | None = None) -> list[WorkItemEvent]:
        """
        Return the sanitized audit trail, oldest first.

        :param conversation_id: Owning session.
        :param item_id: Restrict to one item, or ``None`` for the whole session.
        :returns: List of :class:`WorkItemEvent`.
        """
        ...

    @abstractmethod
    def list_related_projects(self, conversation_id: str) -> list[str]:
        """
        Return the session's related project ids (its home project excluded).

        :param conversation_id: Owning session.
        :returns: Project ids ordered by when they were related.
        """
        ...

    @abstractmethod
    def add_related_project(self, conversation_id: str, project_id: str) -> bool:
        """
        Relate a project to this session. Idempotent.

        :param conversation_id: Owning session.
        :param project_id: The project to relate.
        :returns: ``True`` if newly added, ``False`` if already related.
        """
        ...

    @abstractmethod
    def remove_related_project(self, conversation_id: str, project_id: str) -> bool:
        """
        Unrelate a project from this session. Idempotent.

        :param conversation_id: Owning session.
        :param project_id: The project to unrelate.
        :returns: ``True`` if removed, ``False`` if it was not related.
        """
        ...
