// Tests for the Work Tree API client — the snake_case ↔ camelCase boundary
// and the request shapes the durable endpoints expect. Every mutation must
// carry the caller's `version`; that is what turns a lost update into a
// visible 409 instead of a silent overwrite.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ authenticatedFetch: vi.fn() }));
vi.mock("./identity", () => ({ authenticatedFetch: identity.authenticatedFetch }));

import type { WorkItemWire } from "./workTreeApi";
import {
  createWorkItem,
  deferWorkItem,
  deleteWorkItem,
  getWorkTree,
  isVersionConflict,
  resumeWorkItem,
  summarizeWorkItems,
  updateWorkItem,
  workTreeFromWire,
} from "./workTreeApi";
import { ApiError } from "./sessionsApi";

const SESSION = "conv_abc123";

function wireItem(overrides: Partial<WorkItemWire> = {}): WorkItemWire {
  return {
    id: "wi_1",
    conversation_id: SESSION,
    parent_id: null,
    depth: 1,
    title: "Task",
    brief: null,
    why: null,
    next_action: null,
    status: "not_started",
    delivery_state: null,
    project_id: null,
    source_kind: "user",
    source_ref: null,
    discovery_class: null,
    evidence: null,
    sort_order: 0,
    collapsed: false,
    version: 1,
    created_at: 10,
    updated_at: null,
    completed_at: null,
    deferred_at: null,
    ...overrides,
  };
}

function ok(body: unknown) {
  return { ok: true, status: 200, json: async () => body } as unknown as Response;
}

function lastCall() {
  const calls = identity.authenticatedFetch.mock.calls;
  return calls[calls.length - 1] as [string, RequestInit | undefined];
}

function lastBody(): Record<string, unknown> {
  return JSON.parse(String(lastCall()[1]?.body)) as Record<string, unknown>;
}

beforeEach(() => identity.authenticatedFetch.mockReset());
afterEach(() => vi.clearAllMocks());

describe("workTreeFromWire", () => {
  it("converts the wire shape to the UI shape", () => {
    const tree = workTreeFromWire({
      session_id: SESSION,
      data: [
        wireItem({
          next_action: "Open the PR",
          delivery_state: "merged",
          source_kind: "provider_todo",
          discovery_class: "related_later",
          deferred_at: 99,
        }),
      ],
      related_project_ids: ["proj_1"],
    });
    const [item] = tree.items;
    expect(tree.sessionId).toBe(SESSION);
    expect(item.nextAction).toBe("Open the PR");
    expect(item.deliveryState).toBe("merged");
    expect(item.sourceKind).toBe("provider_todo");
    expect(item.discoveryClass).toBe("related_later");
    expect(item.deferredAt).toBe(99);
    expect(tree.relatedProjectIds).toEqual(["proj_1"]);
  });

  it("reads a session with no items as an empty tree", () => {
    const tree = workTreeFromWire({ session_id: SESSION, data: [], related_project_ids: [] });
    expect(tree.items).toEqual([]);
  });
});

describe("summarizeWorkItems", () => {
  it("treats an empty implementation tree as not started", () => {
    expect(summarizeWorkItems([])).toEqual({
      completed: 0,
      total: 0,
      percent: 0,
      status: "not_started",
    });
  });

  it("reports progress and prioritizes an attention status", () => {
    expect(
      summarizeWorkItems([{ status: "done" }, { status: "working" }, { status: "blocked" }]),
    ).toEqual({ completed: 1, total: 3, percent: 33, status: "blocked" });
  });

  it("reports done only when every implementation step is done", () => {
    expect(summarizeWorkItems([{ status: "done" }, { status: "done" }])).toEqual({
      completed: 2,
      total: 2,
      percent: 100,
      status: "done",
    });
  });
});

describe("getWorkTree", () => {
  it("reads the durable endpoint", async () => {
    identity.authenticatedFetch.mockResolvedValue(
      ok({ session_id: SESSION, data: [wireItem()], related_project_ids: [] }),
    );
    const tree = await getWorkTree(SESSION);
    expect(lastCall()[0]).toBe(`/v1/sessions/${SESSION}/work-tree`);
    expect(tree.items.map((i) => i.title)).toEqual(["Task"]);
  });
});

describe("createWorkItem", () => {
  it("sends only the fields the caller set", async () => {
    identity.authenticatedFetch.mockResolvedValue(ok(wireItem()));
    await createWorkItem(SESSION, { title: "New work" });
    expect(lastCall()[0]).toBe(`/v1/sessions/${SESSION}/work-items`);
    expect(lastBody()).toEqual({ title: "New work" });
  });

  it("maps camelCase fields onto the wire names", async () => {
    identity.authenticatedFetch.mockResolvedValue(ok(wireItem()));
    await createWorkItem(SESSION, {
      title: "Subtask",
      parentId: "wi_parent",
      nextAction: "Run the tests",
      deliveryState: "committed",
    });
    expect(lastBody()).toEqual({
      title: "Subtask",
      parent_id: "wi_parent",
      next_action: "Run the tests",
      delivery_state: "committed",
    });
  });
});

describe("updateWorkItem", () => {
  it("always carries the version the caller last saw", async () => {
    identity.authenticatedFetch.mockResolvedValue(ok(wireItem({ version: 3 })));
    await updateWorkItem(SESSION, "wi_1", { version: 2, status: "working" });
    expect(lastCall()[1]?.method).toBe("PATCH");
    expect(lastBody()).toEqual({ version: 2, status: "working" });
  });

  it("passes an explicit null through so a field can be cleared", async () => {
    // WHY: omitting a field means "leave it alone", so clearing has to be a
    // distinct, explicit null rather than an absent key.
    identity.authenticatedFetch.mockResolvedValue(ok(wireItem()));
    await updateWorkItem(SESSION, "wi_1", { version: 1, brief: null });
    expect(lastBody()).toEqual({ version: 1, brief: null });
  });

  it("sends a reorder as sort_index", async () => {
    identity.authenticatedFetch.mockResolvedValue(ok(wireItem()));
    await updateWorkItem(SESSION, "wi_1", { version: 1, sortIndex: 0 });
    expect(lastBody()).toEqual({ version: 1, sort_index: 0 });
  });
});

describe("deleteWorkItem", () => {
  it("passes the version as a query parameter", async () => {
    identity.authenticatedFetch.mockResolvedValue({ ok: true, status: 204 } as Response);
    await deleteWorkItem(SESSION, "wi_1", 4);
    expect(lastCall()[0]).toBe(`/v1/sessions/${SESSION}/work-items/wi_1?version=4`);
    expect(lastCall()[1]?.method).toBe("DELETE");
  });

  it("throws when the server refuses", async () => {
    identity.authenticatedFetch.mockResolvedValue({
      ok: false,
      status: 409,
      statusText: "Conflict",
      json: async () => ({ error: { code: "conflict", message: "still has children" } }),
    } as unknown as Response);
    await expect(deleteWorkItem(SESSION, "wi_1", 1)).rejects.toBeInstanceOf(ApiError);
  });
});

describe("defer and resume", () => {
  it("posts the version to defer", async () => {
    identity.authenticatedFetch.mockResolvedValue(ok(wireItem({ status: "paused" })));
    const item = await deferWorkItem(SESSION, "wi_1", 2);
    expect(lastCall()[0]).toBe(`/v1/sessions/${SESSION}/work-items/wi_1/defer`);
    expect(lastBody()).toEqual({ version: 2 });
    expect(item.status).toBe("paused");
  });

  it("posts the version to resume", async () => {
    identity.authenticatedFetch.mockResolvedValue(ok(wireItem({ status: "working" })));
    await resumeWorkItem(SESSION, "wi_1", 5);
    expect(lastCall()[0]).toBe(`/v1/sessions/${SESSION}/work-items/wi_1/resume`);
    expect(lastBody()).toEqual({ version: 5 });
  });
});

describe("isVersionConflict", () => {
  it("recognises the server's 409", () => {
    expect(isVersionConflict(new ApiError("stale", 409, "conflict"))).toBe(true);
  });

  it("does not treat other failures as conflicts", () => {
    expect(isVersionConflict(new ApiError("gone", 404, "not_found"))).toBe(false);
    expect(isVersionConflict(new Error("network"))).toBe(false);
  });
});
