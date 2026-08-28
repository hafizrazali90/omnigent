// Typed client for the durable session Work Tree
// (`/v1/sessions/{id}/work-tree` and `/v1/sessions/{id}/work-items`).
// Mirrors `omnigent/server/routes/sessions/routes_work_tree.py`.
//
// Naming: TS surface is camelCase; the wire is snake_case. The helpers
// below convert at the boundary so callers never see raw wire fields.
//
// Every mutation carries the `version` the caller last saw. A stale write
// comes back as a 409 `ApiError` the caller surfaces, rather than silently
// overwriting a concurrent edit in another window.

import { ApiError, apiErrorFromResponse } from "./sessionsApi";
import { authenticatedFetch } from "./identity";

/** How the work itself is going. Independent of {@link WorkDeliveryState}. */
export type WorkStatus = "not_started" | "working" | "waiting" | "paused" | "blocked" | "done";

/**
 * How far a change has actually travelled toward production. Deliberately
 * separate from {@link WorkStatus}: a `done` item is not a shipped one.
 */
export type WorkDeliveryState =
  | "local"
  | "committed"
  | "pushed"
  | "pr_open"
  | "merged"
  | "deployed"
  | "live_checked"
  | "monitored"
  | "accepted"
  | "closed";

/** Where an item came from. */
export type WorkSourceKind =
  "user" | "orchestrator" | "worker" | "provider_todo" | "discovered" | "resumed";

/** How a discovered item relates to the work that surfaced it. */
export type WorkDiscoveryClass = "required" | "related_later" | "unrelated" | "scope_change";

/** The display order of {@link WorkStatus}, used by status pickers. */
export const WORK_STATUSES: readonly WorkStatus[] = [
  "not_started",
  "working",
  "waiting",
  "paused",
  "blocked",
  "done",
];

/** Plain-language labels shared by tree rows and aggregate progress summaries. */
export const WORK_STATUS_LABEL: Record<WorkStatus, string> = {
  not_started: "Not started",
  working: "Working",
  waiting: "Waiting",
  paused: "Paused",
  blocked: "Blocked",
  done: "Done",
};

export interface WorkProgressSummary {
  completed: number;
  total: number;
  percent: number;
  status: WorkStatus;
}

/**
 * Roll a tree up into one honest progress/status summary.
 * Attention states win, then active work; an empty tree is not started.
 */
export function summarizeWorkItems(
  items: readonly Pick<WorkItem, "status">[],
): WorkProgressSummary {
  const total = items.length;
  const completed = items.filter((item) => item.status === "done").length;
  let status: WorkStatus = "not_started";
  if (total > 0 && completed === total) status = "done";
  else if (items.some((item) => item.status === "blocked")) status = "blocked";
  else if (items.some((item) => item.status === "waiting")) status = "waiting";
  else if (items.some((item) => item.status === "working")) status = "working";
  else if (items.some((item) => item.status === "paused")) status = "paused";

  return {
    completed,
    total,
    percent: total === 0 ? 0 : Math.round((completed / total) * 100),
    status,
  };
}

/** One node of a session's Work Tree. */
export interface WorkItem {
  id: string;
  conversationId: string;
  parentId: string | null;
  depth: number;
  title: string;
  brief: string | null;
  why: string | null;
  nextAction: string | null;
  status: WorkStatus;
  deliveryState: WorkDeliveryState | null;
  projectId: string | null;
  sourceKind: WorkSourceKind;
  sourceRef: string | null;
  discoveryClass: WorkDiscoveryClass | null;
  evidence: string | null;
  sortOrder: number;
  collapsed: boolean;
  version: number;
  createdAt: number;
  updatedAt: number | null;
  completedAt: number | null;
  deferredAt: number | null;
}

/** A session's full tree. Always full state, never a delta. */
export interface WorkTree {
  sessionId: string;
  items: WorkItem[];
  relatedProjectIds: string[];
}

/** Wire shape of one work item, as the server sends it. */
export interface WorkItemWire {
  id: string;
  conversation_id: string;
  parent_id: string | null;
  depth: number;
  title: string;
  brief: string | null;
  why: string | null;
  next_action: string | null;
  status: WorkStatus;
  delivery_state: WorkDeliveryState | null;
  project_id: string | null;
  source_kind: WorkSourceKind;
  source_ref: string | null;
  discovery_class: WorkDiscoveryClass | null;
  evidence: string | null;
  sort_order: number;
  collapsed: boolean;
  version: number;
  created_at: number;
  updated_at: number | null;
  completed_at: number | null;
  deferred_at: number | null;
}

/** Wire shape of `GET /v1/sessions/{id}/work-tree` and the SSE payload. */
export interface WorkTreeWire {
  session_id: string;
  data: WorkItemWire[];
  related_project_ids: string[];
}

/**
 * Convert one work item from the wire to the UI type.
 *
 * @param wire - Snake-case work item returned by the server.
 * @returns CamelCase work item for UI code.
 */
function workItemFromWire(wire: WorkItemWire): WorkItem {
  return {
    id: wire.id,
    conversationId: wire.conversation_id,
    parentId: wire.parent_id ?? null,
    depth: wire.depth,
    title: wire.title,
    brief: wire.brief ?? null,
    why: wire.why ?? null,
    nextAction: wire.next_action ?? null,
    status: wire.status,
    deliveryState: wire.delivery_state ?? null,
    projectId: wire.project_id ?? null,
    sourceKind: wire.source_kind,
    sourceRef: wire.source_ref ?? null,
    discoveryClass: wire.discovery_class ?? null,
    evidence: wire.evidence ?? null,
    sortOrder: wire.sort_order,
    collapsed: wire.collapsed ?? false,
    version: wire.version,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at ?? null,
    completedAt: wire.completed_at ?? null,
    deferredAt: wire.deferred_at ?? null,
  };
}

/**
 * Convert a work tree payload from the wire to the UI type.
 *
 * Shared by the fetch path and the `session.work_tree` SSE handler so a
 * reconnect and a live update produce identical state.
 *
 * @param wire - Snake-case tree body.
 * @returns CamelCase tree for UI code.
 */
export function workTreeFromWire(wire: WorkTreeWire): WorkTree {
  return {
    sessionId: wire.session_id,
    items: (wire.data ?? []).map(workItemFromWire),
    relatedProjectIds: wire.related_project_ids ?? [],
  };
}

async function readJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) throw await apiErrorFromResponse(res);
  return (await res.json()) as T;
}

/** True when an error is the server's optimistic-concurrency conflict. */
export function isVersionConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

function workTreeUrl(sessionId: string): string {
  return `/v1/sessions/${encodeURIComponent(sessionId)}/work-tree`;
}

function workItemsUrl(sessionId: string): string {
  return `/v1/sessions/${encodeURIComponent(sessionId)}/work-items`;
}

/**
 * Read a session's current Work Tree.
 *
 * This is what a reload reads, so it is the recovery path when a live
 * event was missed.
 *
 * @param sessionId - Session identifier, e.g. `"conv_abc123"`.
 * @returns The full tree; empty for a session that has never had an item.
 */
export async function getWorkTree(sessionId: string): Promise<WorkTree> {
  const res = await authenticatedFetch(workTreeUrl(sessionId));
  return workTreeFromWire(await readJsonOrThrow<WorkTreeWire>(res));
}

/** Fields accepted when creating an item. */
export interface CreateWorkItemInput {
  title: string;
  parentId?: string | null;
  brief?: string | null;
  why?: string | null;
  nextAction?: string | null;
  status?: WorkStatus;
  deliveryState?: WorkDeliveryState | null;
  projectId?: string | null;
  evidence?: string | null;
}

/**
 * Create one item, appended after its existing siblings.
 *
 * @param sessionId - Session identifier.
 * @param input - The item to create.
 * @returns The created item.
 */
export async function createWorkItem(
  sessionId: string,
  input: CreateWorkItemInput,
): Promise<WorkItem> {
  const res = await authenticatedFetch(workItemsUrl(sessionId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: input.title,
      ...(input.parentId != null ? { parent_id: input.parentId } : {}),
      ...(input.brief !== undefined ? { brief: input.brief } : {}),
      ...(input.why !== undefined ? { why: input.why } : {}),
      ...(input.nextAction !== undefined ? { next_action: input.nextAction } : {}),
      ...(input.status !== undefined ? { status: input.status } : {}),
      ...(input.deliveryState !== undefined ? { delivery_state: input.deliveryState } : {}),
      ...(input.projectId !== undefined ? { project_id: input.projectId } : {}),
      ...(input.evidence !== undefined ? { evidence: input.evidence } : {}),
    }),
  });
  return workItemFromWire(await readJsonOrThrow<WorkItemWire>(res));
}

/**
 * Fields accepted when updating an item. Omitted fields stay unchanged;
 * an explicit `null` clears the field.
 */
export interface UpdateWorkItemInput {
  version: number;
  title?: string;
  brief?: string | null;
  why?: string | null;
  nextAction?: string | null;
  status?: WorkStatus;
  deliveryState?: WorkDeliveryState | null;
  projectId?: string | null;
  evidence?: string | null;
  collapsed?: boolean;
  /** New 0-based position among this item's own siblings. */
  sortIndex?: number;
}

/**
 * Update or reorder one item.
 *
 * @param sessionId - Session identifier.
 * @param itemId - The item to change.
 * @param input - Fields to change plus the version last seen.
 * @returns The updated item.
 * @throws ApiError - 409 when another window already moved this item on.
 */
export async function updateWorkItem(
  sessionId: string,
  itemId: string,
  input: UpdateWorkItemInput,
): Promise<WorkItem> {
  const res = await authenticatedFetch(`${workItemsUrl(sessionId)}/${encodeURIComponent(itemId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      version: input.version,
      ...(input.title !== undefined ? { title: input.title } : {}),
      ...(input.brief !== undefined ? { brief: input.brief } : {}),
      ...(input.why !== undefined ? { why: input.why } : {}),
      ...(input.nextAction !== undefined ? { next_action: input.nextAction } : {}),
      ...(input.status !== undefined ? { status: input.status } : {}),
      ...(input.deliveryState !== undefined ? { delivery_state: input.deliveryState } : {}),
      ...(input.projectId !== undefined ? { project_id: input.projectId } : {}),
      ...(input.evidence !== undefined ? { evidence: input.evidence } : {}),
      ...(input.collapsed !== undefined ? { collapsed: input.collapsed } : {}),
      ...(input.sortIndex !== undefined ? { sort_index: input.sortIndex } : {}),
    }),
  });
  return workItemFromWire(await readJsonOrThrow<WorkItemWire>(res));
}

/**
 * Delete one childless item.
 *
 * @param sessionId - Session identifier.
 * @param itemId - The item to delete.
 * @param version - The version the caller last saw.
 * @throws ApiError - 409 when the item still has children, or the version
 *   is stale.
 */
export async function deleteWorkItem(
  sessionId: string,
  itemId: string,
  version: number,
): Promise<void> {
  const res = await authenticatedFetch(
    `${workItemsUrl(sessionId)}/${encodeURIComponent(itemId)}?version=${version}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw await apiErrorFromResponse(res);
}

/**
 * Defer one item — paused, with a deferred stamp, in one step.
 *
 * @param sessionId - Session identifier.
 * @param itemId - The item to defer.
 * @param version - The version the caller last saw.
 * @returns The deferred item.
 */
export async function deferWorkItem(
  sessionId: string,
  itemId: string,
  version: number,
): Promise<WorkItem> {
  const res = await authenticatedFetch(
    `${workItemsUrl(sessionId)}/${encodeURIComponent(itemId)}/defer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    },
  );
  return workItemFromWire(await readJsonOrThrow<WorkItemWire>(res));
}

/**
 * Resume one deferred item — working, with the deferred stamp cleared.
 *
 * @param sessionId - Session identifier.
 * @param itemId - The item to resume.
 * @param version - The version the caller last saw.
 * @returns The resumed item.
 */
export async function resumeWorkItem(
  sessionId: string,
  itemId: string,
  version: number,
): Promise<WorkItem> {
  const res = await authenticatedFetch(
    `${workItemsUrl(sessionId)}/${encodeURIComponent(itemId)}/resume`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ version }),
    },
  );
  return workItemFromWire(await readJsonOrThrow<WorkItemWire>(res));
}
