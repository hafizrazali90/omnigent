"""Durable session Work Tree routes.

Session-scoped CRUD over the provider-neutral Work Tree. Every successful
mutation publishes the **whole** tree as a ``session.work_tree`` event, and the
session stream's snapshot-on-connect carries the same payload — so a client that
missed an event, reloaded, or reconnected recovers the current tree without
replaying anything.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response

from omnigent.entities.work_item import WorkItem
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime import session_stream
from omnigent.server.auth import (
    LEVEL_EDIT,
    LEVEL_READ,
    AuthProvider,
)
from omnigent.server.routes._auth_helpers import (
    get_user_id as _get_user_id,
)
from omnigent.server.routes._auth_helpers import (
    require_access_and_level as _require_access_and_level,
)
from omnigent.server.routes._errors import session_not_found as _session_not_found
from omnigent.server.schemas import (
    WorkItemCreate,
    WorkItemObject,
    WorkItemUpdate,
    WorkItemVersion,
    WorkTree,
)
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.work_tree_store import WorkTreeStore


def _to_schema(item: WorkItem) -> WorkItemObject:
    """
    Convert a :class:`WorkItem` entity to its API shape.

    :param item: The stored work item.
    :returns: The :class:`WorkItemObject` sent to clients.
    """
    return WorkItemObject(
        id=item.id,
        conversation_id=item.conversation_id,
        parent_id=item.parent_id,
        depth=item.depth,
        title=item.title,
        brief=item.brief,
        why=item.why,
        next_action=item.next_action,
        status=item.status,
        delivery_state=item.delivery_state,
        project_id=item.project_id,
        source_kind=item.source_kind,
        source_ref=item.source_ref,
        discovery_class=item.discovery_class,
        evidence=item.evidence,
        sort_order=item.sort_order,
        collapsed=item.collapsed,
        version=item.version,
        created_at=item.created_at,
        updated_at=item.updated_at,
        completed_at=item.completed_at,
        deferred_at=item.deferred_at,
    )


async def read_work_tree(store: WorkTreeStore, session_id: str) -> WorkTree:
    """
    Read a session's full tree plus its related projects.

    :param store: The work tree store.
    :param session_id: Session/conversation identifier.
    :returns: The full :class:`WorkTree` for this session.
    """
    items = await asyncio.to_thread(store.list_tree, session_id)
    related = await asyncio.to_thread(store.list_related_projects, session_id)
    return WorkTree(
        session_id=session_id,
        data=[_to_schema(item) for item in items],
        related_project_ids=list(related),
    )


def work_tree_event(tree: WorkTree) -> dict[str, Any]:
    """
    Build the full-state ``session.work_tree`` SSE payload.

    Always the whole tree: a delta would leave a client that missed one event
    permanently out of sync, which is exactly the failure the durable tree
    exists to prevent.

    :param tree: The current tree.
    :returns: The event dict to publish.
    """
    return {
        "type": "session.work_tree",
        "conversation_id": tree.session_id,
        "work_tree": tree.model_dump(mode="json"),
    }


async def publish_work_tree(store: WorkTreeStore, session_id: str) -> WorkTree:
    """
    Read the tree and broadcast it to every open stream on this session.

    :param store: The work tree store.
    :param session_id: Session/conversation identifier.
    :returns: The tree that was published.
    """
    tree = await read_work_tree(store, session_id)
    session_stream.publish(session_id, work_tree_event(tree))
    return tree


def register_work_tree_routes(
    router: APIRouter,
    *,
    conversation_store: ConversationStore,
    work_tree_store: WorkTreeStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> None:
    """
    Register the Work Tree routes on ``router``.

    :param router: The sessions router to register on.
    :param conversation_store: Used to confirm the session exists.
    :param work_tree_store: Backing store for the tree.
    :param auth_provider: Resolves the calling user.
    :param permission_store: Session ACL lookups.
    """

    async def _authorize(request: Request, session_id: str, level: int) -> None:
        """Refuse the request unless the caller holds ``level`` on the session."""
        user_id = _get_user_id(request, auth_provider)
        access = await _require_access_and_level(
            user_id, session_id, level, permission_store, conversation_store
        )
        if access.conversation is None:
            conv = await asyncio.to_thread(conversation_store.get_conversation, session_id)
            if conv is None:
                raise _session_not_found()

    def _actor(request: Request) -> str | None:
        """The audit actor for this request — a user id, never a credential."""
        return _get_user_id(request, auth_provider)

    # ── GET /sessions/{session_id}/work-tree ─────────────

    @router.get(
        "/sessions/{session_id}/work-tree",
        response_model=None,
        responses={200: {"model": WorkTree}},
    )
    async def get_work_tree(request: Request, session_id: str) -> WorkTree:
        """
        Return the session's full Work Tree.

        A session that has never had an item returns an empty tree — that is
        the migrated state of every session predating this feature, not an
        error.

        :param request: The FastAPI request, used for auth.
        :param session_id: Session/conversation identifier.
        :returns: The full :class:`WorkTree`.
        :raises OmnigentError: 404 if no session exists.
        """
        await _authorize(request, session_id, LEVEL_READ)
        return await read_work_tree(work_tree_store, session_id)

    # ── POST /sessions/{session_id}/work-items ───────────

    @router.post(
        "/sessions/{session_id}/work-items",
        response_model=None,
        responses={200: {"model": WorkItemObject}},
    )
    async def create_work_item(
        request: Request, session_id: str, body: WorkItemCreate
    ) -> WorkItemObject:
        """
        Create one item, appended after its existing siblings.

        :param request: The FastAPI request, used for auth.
        :param session_id: Session/conversation identifier.
        :param body: The item to create.
        :returns: The created :class:`WorkItemObject`.
        :raises OmnigentError: 404 if no session exists or the parent is not in
            it; 400 for a blank title, an unknown enum value, or a parent that
            would create a fourth level.
        """
        await _authorize(request, session_id, LEVEL_EDIT)
        item = await asyncio.to_thread(
            lambda: work_tree_store.create(
                session_id,
                title=body.title,
                parent_id=body.parent_id,
                brief=body.brief,
                why=body.why,
                next_action=body.next_action,
                status=body.status,
                delivery_state=body.delivery_state,
                project_id=body.project_id,
                source_kind=body.source_kind,
                source_ref=body.source_ref,
                discovery_class=body.discovery_class,
                evidence=body.evidence,
                actor=_actor(request),
            )
        )
        await publish_work_tree(work_tree_store, session_id)
        return _to_schema(item)

    # ── PATCH /sessions/{session_id}/work-items/{item_id} ─

    @router.patch(
        "/sessions/{session_id}/work-items/{item_id}",
        response_model=None,
        responses={200: {"model": WorkItemObject}},
    )
    async def update_work_item(
        request: Request, session_id: str, item_id: str, body: WorkItemUpdate
    ) -> WorkItemObject:
        """
        Apply a partial update, or reorder the item among its siblings.

        The caller's ``version`` is checked first; a stale write is rejected
        with 409 rather than silently overwriting a concurrent edit. Fields and
        ``sort_index`` are one store call, so a request carrying both either
        applies whole and broadcasts once, or applies not at all.

        :param request: The FastAPI request, used for auth.
        :param session_id: Session/conversation identifier.
        :param item_id: The item to update.
        :param body: The fields to change and the version last seen.
        :returns: The updated :class:`WorkItemObject`.
        :raises OmnigentError: 404 if the item is not in this session; 409 if
            the version is stale; 400 for an unknown enum value.
        """
        await _authorize(request, session_id, LEVEL_EDIT)
        actor = _actor(request)
        fields = body.model_dump(exclude_unset=True)
        version = fields.pop("version")
        sort_index = fields.pop("sort_index", None)

        if not fields and sort_index is None:
            # A body carrying only a version is a no-op; echo current state
            # rather than inventing a mutation.
            current = await asyncio.to_thread(work_tree_store.get, session_id, item_id)
            if current is None:
                raise OmnigentError(f"work item {item_id!r} not found", code=ErrorCode.NOT_FOUND)
            return _to_schema(current)

        item: WorkItem = await asyncio.to_thread(
            lambda: work_tree_store.update(
                session_id,
                item_id,
                expected_version=version,
                actor=actor,
                sort_index=sort_index,
                **fields,
            )
        )
        await publish_work_tree(work_tree_store, session_id)
        return _to_schema(item)

    # ── DELETE /sessions/{session_id}/work-items/{item_id} ─

    @router.delete("/sessions/{session_id}/work-items/{item_id}", response_model=None)
    async def delete_work_item(
        request: Request, session_id: str, item_id: str, version: int
    ) -> Response:
        """
        Delete a childless item. A parent with children fails with 409.

        :param request: The FastAPI request, used for auth.
        :param session_id: Session/conversation identifier.
        :param item_id: The item to delete.
        :param version: The version the caller last saw.
        :returns: ``204 No Content``.
        :raises OmnigentError: 404 if the item is not in this session; 409 if
            the version is stale or the item still has children.
        """
        await _authorize(request, session_id, LEVEL_EDIT)
        await asyncio.to_thread(
            lambda: work_tree_store.delete(
                session_id, item_id, expected_version=version, actor=_actor(request)
            )
        )
        await publish_work_tree(work_tree_store, session_id)
        return Response(status_code=204)

    # ── POST …/work-items/{item_id}/defer and /resume ────

    @router.post(
        "/sessions/{session_id}/work-items/{item_id}/defer",
        response_model=None,
        responses={200: {"model": WorkItemObject}},
    )
    async def defer_work_item(
        request: Request, session_id: str, item_id: str, body: WorkItemVersion
    ) -> WorkItemObject:
        """
        Defer an item — paused, with a deferred stamp, in one step.

        :param request: The FastAPI request, used for auth.
        :param session_id: Session/conversation identifier.
        :param item_id: The item to defer.
        :param body: The version the caller last saw.
        :returns: The deferred :class:`WorkItemObject`.
        :raises OmnigentError: 404 if the item is not in this session; 409 if
            the version is stale.
        """
        await _authorize(request, session_id, LEVEL_EDIT)
        item = await asyncio.to_thread(
            lambda: work_tree_store.defer(
                session_id, item_id, expected_version=body.version, actor=_actor(request)
            )
        )
        await publish_work_tree(work_tree_store, session_id)
        return _to_schema(item)

    @router.post(
        "/sessions/{session_id}/work-items/{item_id}/resume",
        response_model=None,
        responses={200: {"model": WorkItemObject}},
    )
    async def resume_work_item(
        request: Request, session_id: str, item_id: str, body: WorkItemVersion
    ) -> WorkItemObject:
        """
        Resume a deferred item — working, with the deferred stamp cleared.

        :param request: The FastAPI request, used for auth.
        :param session_id: Session/conversation identifier.
        :param item_id: The item to resume.
        :param body: The version the caller last saw.
        :returns: The resumed :class:`WorkItemObject`.
        :raises OmnigentError: 404 if the item is not in this session; 409 if
            the version is stale.
        """
        await _authorize(request, session_id, LEVEL_EDIT)
        item = await asyncio.to_thread(
            lambda: work_tree_store.resume(
                session_id, item_id, expected_version=body.version, actor=_actor(request)
            )
        )
        await publish_work_tree(work_tree_store, session_id)
        return _to_schema(item)
