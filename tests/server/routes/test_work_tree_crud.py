"""Tests for the session Work Tree routes (``/v1/sessions/{id}/work-tree``).

The Work Tree router is only mounted when ``create_app`` receives a
``work_tree_store``, so these tests build their own app/client that include one.

Two auth setups are exercised, mirroring ``test_projects_crud``:

- **Single-user** (``client``) — no auth provider, the OSS/local default.
- **Multi-user** (``multi_user_client``) — header auth, used to prove a user
  who cannot reach a session cannot reach its tree either.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from omnigent.db.utils import generate_agent_id
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.app import create_app
from omnigent.server.auth import LEVEL_MANAGE, UnifiedAuthProvider
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import (
    SqlAlchemyConversationStore,
)
from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
from omnigent.stores.permission_store.sqlalchemy_store import (
    SqlAlchemyPermissionStore,
)
from omnigent.stores.work_tree_store.sqlalchemy_store import SqlAlchemyWorkTreeStore

ALICE = "alice@example.com"
BOB = "bob@example.com"

MISSING_ID = "0" * 32


def _as_user(user: str) -> dict[str, str]:
    """Header identifying the requesting user under header auth."""
    return {"X-Forwarded-Email": user}


def _build_app(db_uri: str, tmp_path: Path, *, header_auth: bool) -> FastAPI:
    """Build a Work Tree-enabled app, optionally under header auth."""
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
        # A permission store is only legal alongside an auth provider — the
        # single-user app is the OSS default, where every caller is the owner.
        permission_store=SqlAlchemyPermissionStore(db_uri) if header_auth else None,
        work_tree_store=SqlAlchemyWorkTreeStore(db_uri),
        auth_provider=UnifiedAuthProvider(source="header") if header_auth else None,
    )


@pytest.fixture()
def work_tree_app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """Build a single-user FastAPI app that includes the work tree store."""
    return _build_app(db_uri, tmp_path, header_auth=False)


@pytest_asyncio.fixture()
async def client(work_tree_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the work-tree-enabled app."""
    transport = httpx.ASGITransport(app=work_tree_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture()
async def session_id(db_uri: str) -> str:
    """Seed a test agent and conversation, return the session id."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="work-tree-agent", bundle_location="test:///bundle")
    return conv_store.create_conversation(agent_id=agent_id).id


@pytest_asyncio.fixture()
async def other_session_id(db_uri: str) -> str:
    """A second, unrelated session used to prove cross-session isolation."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="other-agent", bundle_location="test:///bundle")
    return conv_store.create_conversation(agent_id=agent_id).id


async def _create(client: httpx.AsyncClient, session_id: str, **payload: object) -> dict:
    """Create one work item and return the response body."""
    body: dict = {"title": "Task"}
    body.update(payload)  # type: ignore[arg-type]
    resp = await client.post(f"/v1/sessions/{session_id}/work-items", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── GET /work-tree ────────────────────────────────────────────────────────


async def test_existing_session_reads_as_an_empty_tree(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A session created before the Work Tree existed reads as empty, not 404."""
    resp = await client.get(f"/v1/sessions/{session_id}/work-tree")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "work_tree"
    assert body["session_id"] == session_id
    assert body["data"] == []
    assert body["related_project_ids"] == []


async def test_work_tree_for_unknown_session_is_404(client: httpx.AsyncClient) -> None:
    """A tree is only ever served for a session that exists."""
    resp = await client.get(f"/v1/sessions/{MISSING_ID}/work-tree")
    assert resp.status_code == 404


# ── POST /work-items ──────────────────────────────────────────────────────


async def test_create_returns_the_item(client: httpx.AsyncClient, session_id: str) -> None:
    """Creating an item echoes it back at version 1, depth 1, no delivery state."""
    item = await _create(client, session_id, title="Ship the work tree")
    assert item["object"] == "work_item"
    assert item["title"] == "Ship the work tree"
    assert item["depth"] == 1
    assert item["status"] == "not_started"
    assert item["delivery_state"] is None
    assert item["version"] == 1


async def test_create_rejects_a_blank_title(client: httpx.AsyncClient, session_id: str) -> None:
    """A whitespace-only title is a 422 rather than an unnamed row."""
    resp = await client.post(f"/v1/sessions/{session_id}/work-items", json={"title": "   "})
    assert resp.status_code == 422


async def test_create_rejects_an_unknown_status(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """An out-of-vocabulary status is refused rather than silently coerced."""
    resp = await client.post(
        f"/v1/sessions/{session_id}/work-items",
        json={"title": "Task", "status": "almost_done"},
    )
    assert resp.status_code >= 400


async def test_create_rejects_an_unknown_field(client: httpx.AsyncClient, session_id: str) -> None:
    """The request model forbids extras, so a typo cannot be silently dropped."""
    resp = await client.post(
        f"/v1/sessions/{session_id}/work-items",
        json={"title": "Task", "delivery": "merged"},
    )
    assert resp.status_code == 422


async def test_create_for_unknown_session_is_404(client: httpx.AsyncClient) -> None:
    """Items cannot be created against a session that does not exist."""
    resp = await client.post(f"/v1/sessions/{MISSING_ID}/work-items", json={"title": "Task"})
    assert resp.status_code == 404


async def test_depth_four_is_rejected(client: httpx.AsyncClient, session_id: str) -> None:
    """The API enforces the three-level limit, returning 400."""
    one = await _create(client, session_id, title="Programme")
    two = await _create(client, session_id, title="Task", parent_id=one["id"])
    three = await _create(client, session_id, title="Subtask", parent_id=two["id"])
    resp = await client.post(
        f"/v1/sessions/{session_id}/work-items",
        json={"title": "Too deep", "parent_id": three["id"]},
    )
    assert resp.status_code == 400


async def test_parent_from_another_session_is_404(
    client: httpx.AsyncClient, session_id: str, other_session_id: str
) -> None:
    """A parent must live in the same session as its child."""
    foreign = await _create(client, other_session_id, title="Elsewhere")
    resp = await client.post(
        f"/v1/sessions/{session_id}/work-items",
        json={"title": "Child", "parent_id": foreign["id"]},
    )
    assert resp.status_code == 404


async def test_tree_lists_parents_before_children(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """The tree comes back in display order, parents first."""
    parent = await _create(client, session_id, title="Task")
    child = await _create(client, session_id, title="Subtask", parent_id=parent["id"])
    body = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert [i["id"] for i in body["data"]] == [parent["id"], child["id"]]


async def test_tree_is_scoped_to_its_session(
    client: httpx.AsyncClient, session_id: str, other_session_id: str
) -> None:
    """One session's items never appear in another session's tree."""
    await _create(client, session_id, title="Mine")
    await _create(client, other_session_id, title="Theirs")
    body = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert [i["title"] for i in body["data"]] == ["Mine"]


# ── PATCH /work-items/{item_id} ───────────────────────────────────────────


async def test_patch_updates_and_bumps_the_version(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A successful patch returns the new state at the next version."""
    item = await _create(client, session_id)
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "status": "working", "why": "It unblocks review"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "working"
    assert body["why"] == "It unblocks review"
    assert body["version"] == item["version"] + 1


async def test_stale_patch_is_409(client: httpx.AsyncClient, session_id: str) -> None:
    """Two windows editing the same item — the second is told, not ignored."""
    item = await _create(client, session_id)
    first = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "title": "First writer wins"},
    )
    assert first.status_code == 200
    second = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "title": "Second writer"},
    )
    assert second.status_code == 409
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert tree["data"][0]["title"] == "First writer wins"


async def test_patch_can_clear_an_optional_field(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Sending an explicit null clears the field rather than being ignored."""
    item = await _create(client, session_id, brief="temporary note")
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "brief": None},
    )
    assert resp.status_code == 200
    assert resp.json()["brief"] is None


async def test_patch_sort_index_reorders_siblings(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """``sort_index`` moves an item among its siblings."""
    await _create(client, session_id, title="A")
    await _create(client, session_id, title="B")
    third = await _create(client, session_id, title="C")
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{third['id']}",
        json={"version": third["version"], "sort_index": 0},
    )
    assert resp.status_code == 200
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert [i["title"] for i in tree["data"]] == ["C", "A", "B"]


@pytest.mark.parametrize(
    ("field", "clearable"),
    [
        ("status", False),
        ("collapsed", False),
        ("title", False),
        ("sort_index", False),
        ("brief", True),
        ("why", True),
        ("delivery_state", True),
        ("evidence", True),
    ],
)
async def test_explicit_null_is_refused_for_fields_that_cannot_be_cleared(
    client: httpx.AsyncClient, session_id: str, field: str, clearable: bool
) -> None:
    """``null`` clears the optional fields and is a 422 for the rest.

    A client sending ``"collapsed": null`` used to have it silently read as
    ``False``, and ``"status": null`` reached the store's codec as a 500. Both
    are now answered at the boundary with a message naming the field, so the
    caller can fix the request instead of guessing what happened.
    """
    item = await _create(client, session_id)
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], field: None},
    )
    if clearable:
        assert resp.status_code == 200
        return
    assert resp.status_code == 422
    assert field in resp.text


async def test_patch_applies_fields_and_reorder_as_one_change(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Renaming and moving in one request is one mutation at one new version."""
    await _create(client, session_id, title="A")
    second = await _create(client, session_id, title="B")

    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{second['id']}",
        json={"version": second["version"], "title": "Renamed", "sort_index": 0},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == second["version"] + 1
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert [i["title"] for i in tree["data"]] == ["Renamed", "A"]


async def test_a_conflicting_combined_patch_applies_neither_half(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A combined patch that loses the race changes nothing at all.

    Running the field update and the reorder as separate store calls let the
    rename commit and the move fail: the caller was told 409 while the title had
    already changed, and no event told the other windows about it.
    """
    await _create(client, session_id, title="A")
    second = await _create(client, session_id, title="B")

    # Another window edits first, so the caller's version is behind.
    ahead = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{second['id']}",
        json={"version": second["version"], "why": "Someone else got here first"},
    )
    assert ahead.status_code == 200

    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{second['id']}",
        json={"version": second["version"], "title": "Renamed", "sort_index": 0},
    )
    assert resp.status_code == 409
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert [i["title"] for i in tree["data"]] == ["A", "B"]


async def test_patch_status_done_leaves_delivery_state_alone(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Finishing the work never implies the change was shipped."""
    item = await _create(client, session_id)
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "status": "done"},
    )
    assert resp.json()["delivery_state"] is None


async def test_patch_unknown_item_is_404(client: httpx.AsyncClient, session_id: str) -> None:
    """Patching an item that does not exist is a 404."""
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{MISSING_ID}",
        json={"version": 1, "status": "done"},
    )
    assert resp.status_code == 404


async def test_patch_through_the_wrong_session_is_404(
    client: httpx.AsyncClient, session_id: str, other_session_id: str
) -> None:
    """An item can only be reached through the session that owns it."""
    item = await _create(client, other_session_id, title="Theirs")
    resp = await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "status": "done"},
    )
    assert resp.status_code == 404


# ── DELETE /work-items/{item_id} ──────────────────────────────────────────


async def test_delete_removes_a_leaf(client: httpx.AsyncClient, session_id: str) -> None:
    """Deleting a childless item empties the tree."""
    item = await _create(client, session_id)
    resp = await client.delete(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        params={"version": item["version"]},
    )
    assert resp.status_code == 204
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert tree["data"] == []


async def test_delete_with_children_is_409(client: httpx.AsyncClient, session_id: str) -> None:
    """A parent refuses to take its children down with it."""
    parent = await _create(client, session_id, title="Task")
    await _create(client, session_id, title="Subtask", parent_id=parent["id"])
    resp = await client.delete(
        f"/v1/sessions/{session_id}/work-items/{parent['id']}",
        params={"version": parent["version"]},
    )
    assert resp.status_code == 409
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert len(tree["data"]) == 2


async def test_stale_delete_is_409(client: httpx.AsyncClient, session_id: str) -> None:
    """Delete honours the same version check as every other mutation."""
    item = await _create(client, session_id)
    await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "status": "working"},
    )
    resp = await client.delete(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        params={"version": item["version"]},
    )
    assert resp.status_code == 409


# ── defer / resume ────────────────────────────────────────────────────────


async def test_defer_and_resume_round_trip(client: httpx.AsyncClient, session_id: str) -> None:
    """Defer pauses and stamps; resume returns to working and clears the stamp."""
    item = await _create(client, session_id)
    deferred = await client.post(
        f"/v1/sessions/{session_id}/work-items/{item['id']}/defer",
        json={"version": item["version"]},
    )
    assert deferred.status_code == 200
    assert deferred.json()["status"] == "paused"
    assert deferred.json()["deferred_at"] is not None

    resumed = await client.post(
        f"/v1/sessions/{session_id}/work-items/{item['id']}/resume",
        json={"version": deferred.json()["version"]},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "working"
    assert resumed.json()["deferred_at"] is None


async def test_stale_defer_is_409(client: httpx.AsyncClient, session_id: str) -> None:
    """Defer refuses a stale version."""
    item = await _create(client, session_id)
    await client.patch(
        f"/v1/sessions/{session_id}/work-items/{item['id']}",
        json={"version": item["version"], "status": "working"},
    )
    resp = await client.post(
        f"/v1/sessions/{session_id}/work-items/{item['id']}/defer",
        json={"version": item["version"]},
    )
    assert resp.status_code == 409


# ── authorization ─────────────────────────────────────────────────────────


@pytest.fixture()
def multi_user_app(runtime_init: None, db_uri: str, tmp_path: Path) -> FastAPI:
    """Build the same app under header auth so requests carry an identity."""
    return _build_app(db_uri, tmp_path, header_auth=True)


@pytest_asyncio.fixture()
async def multi_user_client(multi_user_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client wired to the header-auth app."""
    transport = httpx.ASGITransport(app=multi_user_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
def alice_session_id(db_uri: str) -> str:
    """A session Alice manages; Bob is granted nothing on it."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    conv_store = SqlAlchemyConversationStore(db_uri)
    agent_id = generate_agent_id()
    agent_store.create(agent_id, name="acl-agent", bundle_location="test:///bundle")
    conv = conv_store.create_conversation(agent_id=agent_id)
    SqlAlchemyPermissionStore(db_uri).grant(ALICE, conv.id, LEVEL_MANAGE)
    return conv.id


async def test_another_user_cannot_read_the_tree(
    multi_user_client: httpx.AsyncClient, alice_session_id: str
) -> None:
    """A user with no access to the session cannot read its Work Tree."""
    resp = await multi_user_client.get(
        f"/v1/sessions/{alice_session_id}/work-tree", headers=_as_user(BOB)
    )
    assert resp.status_code in (403, 404)


async def test_another_user_cannot_write_to_the_tree(
    multi_user_client: httpx.AsyncClient, alice_session_id: str
) -> None:
    """A user with no access to the session cannot add to its Work Tree."""
    resp = await multi_user_client.post(
        f"/v1/sessions/{alice_session_id}/work-items",
        json={"title": "Injected"},
        headers=_as_user(BOB),
    )
    assert resp.status_code in (403, 404)


async def test_the_owner_can_read_and_write(
    multi_user_client: httpx.AsyncClient, alice_session_id: str
) -> None:
    """The session owner reaches their own tree normally."""
    created = await multi_user_client.post(
        f"/v1/sessions/{alice_session_id}/work-items",
        json={"title": "Mine"},
        headers=_as_user(ALICE),
    )
    assert created.status_code == 200
    tree = await multi_user_client.get(
        f"/v1/sessions/{alice_session_id}/work-tree", headers=_as_user(ALICE)
    )
    assert [i["title"] for i in tree.json()["data"]] == ["Mine"]


# ── provider observations arriving through the real event path ────────────


async def test_a_provider_todo_event_builds_the_tree(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A forwarder's ``external_session_todos`` post lands in the durable tree.

    Both native forwarders normalize their own events into this one shape, so
    posting it here exercises the same server path Claude and Codex use.
    """
    resp = await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "external_session_todos",
            "data": {
                "todos": [
                    {"content": "Read the code", "status": "completed", "activeForm": "Reading"},
                    {"content": "Write the fix", "status": "in_progress", "activeForm": "Writing"},
                ]
            },
        },
    )
    assert resp.status_code == 202, resp.text
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert [(i["title"], i["status"]) for i in tree["data"]] == [
        ("Read the code", "done"),
        ("Write the fix", "working"),
    ]
    assert {i["source_kind"] for i in tree["data"]} == {"provider_todo"}


async def test_a_provider_todo_event_never_removes_user_work(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """A provider list that omits a user's item leaves that item alone."""
    mine = await _create(client, session_id, title="My own task", why="Matters to me")
    await client.post(
        f"/v1/sessions/{session_id}/events",
        json={
            "type": "external_session_todos",
            "data": {
                "todos": [
                    {"content": "Something else", "status": "pending", "activeForm": "Doing"}
                ]
            },
        },
    )
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    survivor = next(i for i in tree["data"] if i["id"] == mine["id"])
    assert survivor["title"] == "My own task"
    assert survivor["why"] == "Matters to me"


async def test_an_empty_provider_list_does_not_manufacture_a_tree(
    client: httpx.AsyncClient, session_id: str
) -> None:
    """Casual conversation with no real todo leaves the tree empty."""
    await client.post(
        f"/v1/sessions/{session_id}/events",
        json={"type": "external_session_todos", "data": {"todos": []}},
    )
    tree = (await client.get(f"/v1/sessions/{session_id}/work-tree")).json()
    assert tree["data"] == []
