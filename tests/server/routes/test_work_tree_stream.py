"""Tests for Work Tree live delivery and snapshot-on-connect.

The durable tree only earns its name if a client can lose events and still end
up correct. These tests pin the two halves of that contract:

- every mutation broadcasts the **whole** tree, so a client never has to
  reconstruct state from a sequence of deltas; and
- a stream that connects late, or reconnects after a missed event, receives the
  current tree as a snapshot rather than waiting for the next mutation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import TypeAdapter

from omnigent.db.utils import generate_agent_id
from omnigent.runtime import session_stream
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.routes.sessions.routes_work_tree import (
    publish_work_tree,
    read_work_tree,
    work_tree_event,
)
from omnigent.server.schemas import ServerStreamEvent
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.work_tree_store.sqlalchemy_store import SqlAlchemyWorkTreeStore

_STREAM_EVENT = TypeAdapter(ServerStreamEvent)


@pytest.fixture()
def work_tree_store(db_uri: str) -> SqlAlchemyWorkTreeStore:
    """A work tree store backed by the test database."""
    return SqlAlchemyWorkTreeStore(db_uri)


@pytest.fixture()
def stream_app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """Build a Work Tree-enabled app."""
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    return create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifact_store,
        agent_cache=AgentCache(
            artifact_store=artifact_store,
            cache_dir=tmp_path / "cache",
        ),
        work_tree_store=SqlAlchemyWorkTreeStore(db_uri),
    )


@pytest_asyncio.fixture()
async def client(stream_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the work-tree-enabled app."""
    transport = httpx.ASGITransport(app=stream_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def session_id(db_uri: str) -> str:
    """Seed a test agent and conversation, return the session id."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="stream-agent", bundle_location="test:///bundle")
    return conv_store.create_conversation(agent_id=agent_id).id


async def _collect(session_id: str, count: int) -> tuple[list[dict[str, Any]], asyncio.Task[None]]:
    """Subscribe and collect ``count`` events from the session stream.

    :param session_id: The session to subscribe to.
    :param count: How many events to read before the reader stops.
    :returns: The (still filling) event list and the reader task to await.
    """
    received: list[dict[str, Any]] = []
    ready = asyncio.Event()

    async def _reader() -> None:
        agen = session_stream.subscribe(session_id)
        ready.set()
        async for event in agen:
            received.append(event)
            if len(received) >= count:
                break

    task = asyncio.create_task(_reader())
    await ready.wait()
    # Let the subscriber register its queue before the producer publishes;
    # events emitted before a subscriber exists are dropped by design.
    await asyncio.sleep(0.05)
    return received, task


# ── the published payload is always full state ────────────────────────────


async def test_event_carries_the_whole_tree(
    work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """The event is the complete tree, not a delta for the changed item.

    A delta would leave a client that dropped one event permanently wrong,
    which is the exact failure the durable tree exists to prevent.
    """
    work_tree_store.create(session_id, title="First")
    work_tree_store.create(session_id, title="Second")
    tree = await read_work_tree(work_tree_store, session_id)
    event = work_tree_event(tree)
    assert event["type"] == "session.work_tree"
    assert event["conversation_id"] == session_id
    assert [i["title"] for i in event["work_tree"]["data"]] == ["First", "Second"]


async def test_event_validates_against_the_stream_union(
    work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """The payload is a modelled stream event, so the SSE boundary accepts it.

    The route layer validates every emitted dict against ``ServerStreamEvent``;
    an unmodelled event would fail loud at serialization time instead of
    reaching the client.
    """
    work_tree_store.create(session_id, title="Task")
    tree = await read_work_tree(work_tree_store, session_id)
    parsed = _STREAM_EVENT.validate_python(work_tree_event(tree))
    assert parsed.type == "session.work_tree"


async def test_mutation_broadcasts_to_open_subscribers(
    work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """An open stream sees the new tree as soon as a mutation lands."""
    received, task = await _collect(session_id, 1)
    work_tree_store.create(session_id, title="Live update")
    await publish_work_tree(work_tree_store, session_id)
    await asyncio.wait_for(task, timeout=2)
    assert len(received) == 1
    assert received[0]["type"] == "session.work_tree"
    assert [i["title"] for i in received[0]["work_tree"]["data"]] == ["Live update"]


async def test_two_windows_both_receive_the_same_state(
    work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """Every open window converges on identical state after a mutation.

    This is the other half of the optimistic-version guard: the loser of a
    concurrent edit is told (409) *and* both windows then see the same tree.
    """
    first, first_task = await _collect(session_id, 1)
    second, second_task = await _collect(session_id, 1)
    work_tree_store.create(session_id, title="Shared")
    await publish_work_tree(work_tree_store, session_id)
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)
    assert first[0]["work_tree"] == second[0]["work_tree"]


# ── a combined patch is all-or-nothing, and says so exactly once ──────────


async def test_a_combined_patch_broadcasts_one_full_state_event(
    client: httpx.AsyncClient, work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """Rename-and-move is one user action, so it is one event carrying the tree."""
    work_tree_store.create(session_id, title="A")
    second = work_tree_store.create(session_id, title="B")

    received, task = await _collect(session_id, 2)
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{second.id}",
        json={"version": second.version, "title": "Renamed", "sort_index": 0},
    )
    assert resp.status_code == 200
    # A second event would mean the request was still two mutations.
    await asyncio.wait({task}, timeout=0.3)
    task.cancel()

    assert len(received) == 1
    assert [i["title"] for i in received[0]["work_tree"]["data"]] == ["Renamed", "A"]


async def test_a_conflicting_combined_patch_broadcasts_nothing(
    client: httpx.AsyncClient, work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """A rejected combined patch leaves no half-applied state to broadcast.

    The failure this pins: the field update committed, the reorder hit a 409,
    and the publish never ran — so every other window kept showing the old title
    with no event coming to correct it.
    """
    work_tree_store.create(session_id, title="A")
    second = work_tree_store.create(session_id, title="B")
    work_tree_store.update(
        session_id, second.id, expected_version=second.version, why="Another window"
    )

    received, task = await _collect(session_id, 1)
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{second.id}",
        json={"version": second.version, "title": "Renamed", "sort_index": 0},
    )
    assert resp.status_code == 409
    await asyncio.wait({task}, timeout=0.3)
    task.cancel()

    assert received == []
    tree = await read_work_tree(work_tree_store, session_id)
    assert [i.title for i in tree.data] == ["A", "B"]


# ── recovery after a missed event ─────────────────────────────────────────


async def test_a_client_that_missed_an_event_recovers_the_latest_tree(
    work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """A mutation published with nobody listening is recovered on reconnect.

    Events fired while no subscriber is attached are dropped by design, so the
    only thing standing between a disconnected client and a stale tree is the
    snapshot it receives when it comes back.
    """
    work_tree_store.create(session_id, title="Missed while disconnected")
    # Published into the void — no subscriber exists yet.
    await publish_work_tree(work_tree_store, session_id)

    recovered = await read_work_tree(work_tree_store, session_id)
    assert [i.title for i in recovered.data] == ["Missed while disconnected"]


async def test_reload_reads_the_current_tree_from_the_endpoint(
    client: httpx.AsyncClient, work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """A full page reload rebuilds the tree from the durable endpoint."""
    work_tree_store.create(session_id, title="Survives reload")
    body = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert [i["title"] for i in body["data"]] == ["Survives reload"]


async def test_snapshot_matches_the_live_event_payload(
    work_tree_store: SqlAlchemyWorkTreeStore, session_id: str
) -> None:
    """Snapshot and live update are the same shape, so one handler serves both."""
    work_tree_store.create(session_id, title="Task")
    tree = await read_work_tree(work_tree_store, session_id)
    snapshot = work_tree_event(tree)

    received, task = await _collect(session_id, 1)
    await publish_work_tree(work_tree_store, session_id)
    await asyncio.wait_for(task, timeout=2)
    assert received[0] == snapshot
