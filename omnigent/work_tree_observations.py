"""Provider-neutral normalization of todo/plan observations into the Work Tree.

Claude's ``TodoWrite`` and Codex's ``update_plan`` are *observations*, not state.
They say what a provider currently believes it is doing; they do not own the
session's meaning. This module is the single place that reconciles such an
observation with the durable tree, so both harness adapters only ever have to
translate their own event shape into :class:`WorkObservation` and hand it over.

The reconciliation is deliberately conservative and additive:

- an observation may create a node, and may move a node along the **work**
  axis (including to ``done``);
- it may never write a delivery state, never rewrite a title, brief, reason,
  evidence or project a person set, never reopen work a person marked done,
  and never delete anything.

That asymmetry is the whole point. A provider that forgets an item, renames it,
reorders its list, or restarts with a shorter one must not be able to erase work
the user still cares about.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from omnigent.entities.work_item import WorkItem
from omnigent.stores.work_tree_store import WorkTreeStore

# Provider todo vocabulary → the Work Tree's work-status vocabulary. Both
# native forwarders already normalize their own statuses to these three names
# before the event reaches the server.
_STATUS_FROM_PROVIDER: dict[str, str] = {
    "pending": "not_started",
    "in_progress": "working",
    "completed": "done",
}

# Statuses a provider observation must not overwrite. A person who paused,
# deferred or blocked an item said something the provider does not know; a
# routine list refresh should not quietly undo it.
_USER_HELD_STATUSES = frozenset({"paused", "blocked", "waiting"})

# Actor prefix ``apply_observations`` records for provider-driven changes.
_PROVIDER_ACTOR_PREFIX = "provider:"

# Audit actions that set the work status outright. ``updated`` is decided by
# whether "status" appears in its field list.
_STATUS_SETTING_ACTIONS = frozenset({"created", "status_changed", "deferred", "resumed"})

# Collapse case, punctuation and whitespace so "Fix the login bug." and
# "fix the login bug" re-match the same node across a worker restart.
# ``\W`` is Unicode-aware on ``str`` patterns, so CJK, Cyrillic, Arabic, Thai
# and every other script keep their letters; underscore is joined to the class
# so it separates words like any other punctuation.
_NORMALIZE_RE = re.compile(r"[\W_]+")

_WHITESPACE_RE = re.compile(r"\s+")


def _match_key(title: str) -> str:
    """
    Reduce a title to a conservative comparison key.

    Titles are NFKC-normalized first, so a full-width or decomposed spelling of
    the same words re-matches the node a worker created before it restarted.

    A title made only of emoji or punctuation keeps no word characters at all.
    Rather than collapsing every such title to one unmatchable empty key — which
    made each observation create a fresh duplicate — those fall back to the
    verbatim (whitespace-collapsed) text in their own ``r:`` namespace. "🎉"
    therefore re-matches "🎉" and can never match "✅" or an unrelated item.

    :param title: The raw title text.
    :returns: A namespaced comparison key; ``""`` only for a blank title.
    """
    normalized = unicodedata.normalize("NFKC", title).casefold()
    key = _NORMALIZE_RE.sub(" ", normalized).strip()
    if key:
        return f"w:{key}"
    literal = _WHITESPACE_RE.sub(" ", normalized).strip()
    return f"r:{literal}" if literal else ""


@dataclass(frozen=True)
class WorkObservation:
    """
    One normalized provider observation about a piece of work.

    :param title: The step's text as the provider stated it.
    :param status: A Work Tree work status (see ``WORK_STATUSES``).
    :param source_ref: Stable provider handle for this step when the provider
        supplies one, else ``None``. Never a prompt or payload.
    """

    title: str
    status: str
    source_ref: str | None = None


def observations_from_provider_todos(
    todos: list[dict[str, Any]], *, provider: str
) -> list[WorkObservation]:
    """
    Translate the shared provider todo shape into observations.

    Both native forwarders normalize their own events into
    ``{"content", "status", "activeForm"}`` before posting, so this one
    function serves Claude ``TodoWrite`` and Codex ``update_plan`` alike.

    Malformed entries are skipped rather than guessed at: an observation that
    cannot be trusted should change nothing.

    :param todos: The provider's todo list from the event body.
    :param provider: Provider label used to namespace ``source_ref``,
        e.g. ``"claude"`` or ``"codex"``.
    :returns: The observations worth acting on, in the provider's order.
    """
    observations: list[WorkObservation] = []
    for entry in todos:
        if not isinstance(entry, dict):
            continue
        content = entry.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        status = _STATUS_FROM_PROVIDER.get(str(entry.get("status")))
        if status is None:
            continue
        # Providers rarely supply a stable step id today; namespace it when
        # they do so a Claude id can never collide with a Codex one.
        raw_id = entry.get("id")
        source_ref = f"{provider}:{raw_id}" if isinstance(raw_id, str) and raw_id.strip() else None
        observations.append(
            WorkObservation(title=content.strip(), status=status, source_ref=source_ref)
        )
    return observations


def _find_match(
    observation: WorkObservation,
    items: list[WorkItem],
    claimed: set[str],
) -> WorkItem | None:
    """
    Find the node an observation refers to, or ``None`` when there is no safe one.

    Explicit provider handles win. Falling back to a normalized title match is
    what keeps ids stable across a worker restart or a provider switch, when the
    only thing the new provider can offer is the same wording.

    :param observation: The observation to place.
    :param items: Every item in the session's tree.
    :param claimed: Ids already matched by an earlier observation in this batch.
    :returns: The matching :class:`WorkItem`, or ``None``.
    """
    if observation.source_ref is not None:
        for item in items:
            if item.id not in claimed and item.source_ref == observation.source_ref:
                return item
    key = _match_key(observation.title)
    if not key:
        return None
    # Providers emit a flat list of steps, so only a root node can be the thing
    # they mean. Without this a step would bind to a same-titled subtask a
    # person nested under something else and then drive *its* status.
    candidates = [item for item in items if item.depth == 1 and item.id not in claimed]
    # Prefer a node this provider path already owns before adopting one a
    # person wrote, so provider churn stays on provider-created rows.
    for provider_owned in (True, False):
        for item in candidates:
            if (item.source_kind == "provider_todo") is not provider_owned:
                continue
            if _match_key(item.title) == key:
                return item
    return None


def _status_setting_actor(
    store: WorkTreeStore, conversation_id: str, item: WorkItem
) -> str | None:
    """
    Return the actor behind ``item``'s current work status.

    :param store: The work tree store.
    :param conversation_id: Owning session.
    :param item: The item whose status authorship is in question.
    :returns: The actor string, or ``None`` when it cannot be attributed.
    """
    for event in reversed(store.list_events(conversation_id, item.id)):
        if event.action in _STATUS_SETTING_ACTIONS:
            return event.actor
        if event.action == "updated" and event.summary is not None:
            if "status" in {part.strip() for part in event.summary.split(",")}:
                return event.actor
    return None


def _completed_by_a_person(store: WorkTreeStore, conversation_id: str, item: WorkItem) -> bool:
    """
    True when a person, not a provider, marked ``item`` done.

    An unattributable status (no audit entry, or no actor recorded — the
    single-user default has no user id) counts as a person's, because losing a
    tick the user made is far worse than declining one provider downgrade.

    :param store: The work tree store.
    :param conversation_id: Owning session.
    :param item: The ``done`` item an observation wants to reopen.
    :returns: ``True`` when the completion must be preserved.
    """
    actor = _status_setting_actor(store, conversation_id, item)
    return actor is None or not actor.startswith(_PROVIDER_ACTOR_PREFIX)


def apply_observations(
    store: WorkTreeStore,
    conversation_id: str,
    observations: list[WorkObservation],
    *,
    provider: str,
) -> bool:
    """
    Reconcile a provider's observations with the session's durable tree.

    Additive by construction: unmatched observations become new
    ``provider_todo`` items, matched ones may advance along the work axis, and
    anything the provider no longer mentions is left exactly as it was.

    :param store: The work tree store.
    :param conversation_id: Owning session.
    :param observations: Normalized observations, in the provider's order.
    :param provider: Provider label recorded as the audit actor.
    :returns: ``True`` when the tree changed, so the caller should republish.
    """
    if not observations:
        # Casual conversation, or a malformed event, must not manufacture a
        # tree — and must not disturb one that already exists.
        return False

    items = store.list_tree(conversation_id)
    claimed: set[str] = set()
    actor = f"provider:{provider}"
    changed = False

    for observation in observations:
        match = _find_match(observation, items, claimed)
        if match is None:
            store.create(
                conversation_id,
                title=observation.title,
                status=observation.status,
                source_kind="provider_todo",
                source_ref=observation.source_ref,
                actor=actor,
            )
            changed = True
            continue

        claimed.add(match.id)
        if match.deferred_at is not None or match.status in _USER_HELD_STATUSES:
            # The person parked this deliberately; a routine list refresh is
            # not new information about it.
            continue
        if match.status == observation.status:
            continue
        if (
            match.status == "done"
            and observation.status != "done"
            and _completed_by_a_person(store, conversation_id, match)
        ):
            # The user ticked this complete. A provider re-emitting its list
            # from the top — after a restart, a compaction, or a switch to the
            # other harness — is not new information about that decision, and
            # reopening it would also clear the completion stamp.
            continue
        store.update(
            conversation_id,
            match.id,
            expected_version=match.version,
            status=observation.status,
            actor=actor,
        )
        changed = True

    return changed


def apply_provider_todos(
    store: WorkTreeStore,
    conversation_id: str,
    todos: list[dict[str, Any]],
    *,
    provider: str,
) -> bool:
    """
    Translate and apply a provider todo list in one step.

    :param store: The work tree store.
    :param conversation_id: Owning session.
    :param todos: The provider's todo list from the event body.
    :param provider: Provider label, e.g. ``"claude"`` or ``"codex"``.
    :returns: ``True`` when the tree changed.
    """
    return apply_observations(
        store,
        conversation_id,
        observations_from_provider_todos(todos, provider=provider),
        provider=provider,
    )
