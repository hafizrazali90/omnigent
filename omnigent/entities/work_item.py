"""Work item entity — the durable, provider-neutral session Work Tree.

A :class:`WorkItem` is one node of a session's Work Tree: the canonical record
of what the session is doing, why it matters, and how far it has actually got.
It is server state, not provider state — Claude ``TodoWrite`` and Codex
``update_plan`` events are *observations* that may update a node, but they never
own it, so a shorter or reordered provider list can never erase user context.

Two independent axes are deliberately kept apart:

- **work status** — how the thinking/doing is going (``not_started`` …
  ``done``). A provider observation may drive this.
- **delivery state** — how far the change has actually travelled toward
  production (``local`` … ``closed``). Only an explicit, human-or-orchestrator
  signal sets this; a green work status never implies committed, merged, or
  deployed.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Closed vocabularies ────────────────────────────────

#: How the work itself is going. Ordered loosely from untouched to finished.
WORK_STATUSES: tuple[str, ...] = (
    "not_started",
    "working",
    "waiting",
    "paused",
    "blocked",
    "done",
)

#: How far a change has actually travelled. Deliberately separate from
#: :data:`WORK_STATUSES` so "done" can never be read as "shipped".
DELIVERY_STATES: tuple[str, ...] = (
    "local",
    "committed",
    "pushed",
    "pr_open",
    "merged",
    "deployed",
    "live_checked",
    "monitored",
    "accepted",
    "closed",
)

#: Where an item came from. ``provider_todo`` marks a node first seen through a
#: Claude/Codex observation rather than authored by a person.
SOURCE_KINDS: tuple[str, ...] = (
    "user",
    "orchestrator",
    "worker",
    "provider_todo",
    "discovered",
    "resumed",
)

#: How a discovered item relates to the work that surfaced it. Lets the tree
#: hold "we found this, it is not this task's job" without losing it.
DISCOVERY_CLASSES: tuple[str, ...] = (
    "required",
    "related_later",
    "unrelated",
    "scope_change",
)

#: Explicit, user-chosen session lifecycle. Distinct from the computed
#: operational runner state, which must never overwrite a choice made here.
SESSION_LIFECYCLES: tuple[str, ...] = (
    "active",
    "paused",
    "deferred",
    "completed",
    "archived",
)

#: Deepest tree the product allows: session/programme → task → subtask.
MAX_WORK_ITEM_DEPTH = 3


@dataclass
class WorkItem:
    """
    One node of a session's durable Work Tree.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param conversation_id: Owning session. A parent and child always share it.
    :param parent_id: Parent node, or ``None`` for a root (depth 1) item.
    :param depth: 1-based tree depth, capped at :data:`MAX_WORK_ITEM_DEPTH`.
    :param title: Short plain-language name — the one line always shown.
    :param brief: Optional one-sentence expansion of the title.
    :param why: Optional reason this matters, shown in expanded details.
    :param next_action: Optional concrete next step.
    :param status: One of :data:`WORK_STATUSES`.
    :param delivery_state: One of :data:`DELIVERY_STATES`, or ``None`` when the
        item has no delivery meaning yet.
    :param project_id: Project this item belongs to when it differs from the
        session's home project; ``None`` means "same as the session".
    :param source_kind: One of :data:`SOURCE_KINDS`.
    :param source_ref: Safe, non-sensitive provider/orchestrator reference used
        to re-match an item across restarts (e.g. a provider todo id). Never a
        prompt, payload, or credential.
    :param discovery_class: One of :data:`DISCOVERY_CLASSES`, or ``None``.
    :param evidence: Human-readable summary of what proves this item's state.
    :param sort_order: Stable order among siblings under the same parent.
    :param collapsed: User's collapse preference for this node's children.
    :param version: Optimistic-concurrency counter, bumped on every mutation.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    :param completed_at: Unix epoch seconds the item reached ``done``, else
        ``None``. Cleared when the item leaves ``done``.
    :param deferred_at: Unix epoch seconds the item was deferred, else ``None``.
    """

    id: str
    conversation_id: str
    parent_id: str | None
    depth: int
    title: str
    status: str
    source_kind: str
    sort_order: int
    version: int
    created_at: int
    brief: str | None = None
    why: str | None = None
    next_action: str | None = None
    delivery_state: str | None = None
    project_id: str | None = None
    source_ref: str | None = None
    discovery_class: str | None = None
    evidence: str | None = None
    collapsed: bool = False
    updated_at: int | None = None
    completed_at: int | None = None
    deferred_at: int | None = None


@dataclass
class WorkItemEvent:
    """
    One sanitized entry in a work item's append-only audit trail.

    Records *that* a change happened and by whom — never the raw prompt, tool
    payload, or provider text that triggered it.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param conversation_id: Owning session.
    :param item_id: The work item this entry describes.
    :param action: What happened, e.g. ``"created"``, ``"updated"``,
        ``"reordered"``, ``"status_changed"``, ``"deferred"``, ``"resumed"``,
        ``"deleted"``.
    :param actor: Who did it — a user id, or a provider/source label such as
        ``"provider:claude"``. Never a credential.
    :param version: The item version *after* this action.
    :param summary: Optional structural note — the field *names* touched, or a
        position. Never a value the user typed, since entries outlive the item.
    :param created_at: Unix epoch seconds the entry was appended.
    """

    id: str
    conversation_id: str
    item_id: str
    action: str
    actor: str | None
    version: int
    created_at: int
    summary: str | None = None
