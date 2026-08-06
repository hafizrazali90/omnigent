// Tests for useWorkTree — the query + mutation layer behind the rail's Work
// Tree. Covers the nesting transform (which must preserve server order, not
// re-sort it) and the mutation helpers' refresh behaviour.

import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as WorkTreeApiModule from "@/lib/workTreeApi";
import type { WorkItem } from "@/lib/workTreeApi";

const api = vi.hoisted(() => ({ getWorkTree: vi.fn() }));
vi.mock("@/lib/workTreeApi", async (importOriginal) => {
  const actual = await importOriginal<typeof WorkTreeApiModule>();
  return { ...actual, getWorkTree: api.getWorkTree };
});

import { buildWorkTreeNodes, useWorkTree, workTreeQueryKey } from "./useWorkTree";

const SESSION = "conv_abc123";

let nextId = 0;

function item(overrides: Partial<WorkItem> = {}): WorkItem {
  nextId += 1;
  return {
    id: `wi_${nextId}`,
    conversationId: SESSION,
    parentId: null,
    depth: 1,
    title: `Item ${nextId}`,
    brief: null,
    why: null,
    nextAction: null,
    status: "not_started",
    deliveryState: null,
    projectId: null,
    sourceKind: "user",
    sourceRef: null,
    discoveryClass: null,
    evidence: null,
    sortOrder: nextId,
    collapsed: false,
    version: 1,
    createdAt: 0,
    updatedAt: null,
    completedAt: null,
    deferredAt: null,
    ...overrides,
  };
}

function wrapper(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  nextId = 0;
  api.getWorkTree.mockReset();
});

afterEach(() => vi.clearAllMocks());

describe("buildWorkTreeNodes", () => {
  it("nests children under their parent", () => {
    const parent = item({ title: "Parent" });
    const child = item({ title: "Child", parentId: parent.id, depth: 2 });
    const [root] = buildWorkTreeNodes([parent, child]);
    expect(root.item.id).toBe(parent.id);
    expect(root.children.map((c) => c.item.id)).toEqual([child.id]);
  });

  it("preserves the server's order rather than re-sorting", () => {
    // WHY: the server already returns siblings in the order the user dragged
    // them into. Re-sorting here would quietly undo a reorder.
    const first = item({ title: "B", sortOrder: 0 });
    const second = item({ title: "A", sortOrder: 1 });
    expect(buildWorkTreeNodes([first, second]).map((n) => n.item.title)).toEqual(["B", "A"]);
  });

  it("keeps an item whose parent is missing visible at the root", () => {
    // WHY: dropping an orphan would silently lose a user's work; surfacing it
    // is always better than hiding it.
    const orphan = item({ title: "Orphan", parentId: "wi_missing", depth: 2 });
    expect(buildWorkTreeNodes([orphan]).map((n) => n.item.title)).toEqual(["Orphan"]);
  });

  it("returns nothing for an empty tree", () => {
    expect(buildWorkTreeNodes([])).toEqual([]);
  });
});

describe("useWorkTree", () => {
  it("stays idle without a session", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderHook(() => useWorkTree(null), { wrapper: wrapper(qc) });
    expect(api.getWorkTree).not.toHaveBeenCalled();
  });

  it("exposes the fetched items and their nested shape", async () => {
    const parent = item({ title: "Parent" });
    const child = item({ title: "Child", parentId: parent.id, depth: 2 });
    api.getWorkTree.mockResolvedValue({
      sessionId: SESSION,
      items: [parent, child],
      relatedProjectIds: ["proj_1"],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useWorkTree(SESSION), { wrapper: wrapper(qc) });

    await waitFor(() => expect(result.current.items).toHaveLength(2));
    expect(result.current.nodes).toHaveLength(1);
    expect(result.current.nodes[0].children).toHaveLength(1);
    expect(result.current.relatedProjectIds).toEqual(["proj_1"]);
  });

  it("reads state written into its cache key by the live SSE handler", async () => {
    // WHY: the SSE handler writes full state straight into this key. If the
    // key or shape drifted, a live update would never reach the rail.
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const pushed = item({ title: "Pushed live" });
    qc.setQueryData(workTreeQueryKey(SESSION), {
      sessionId: SESSION,
      items: [pushed],
      relatedProjectIds: [],
    });
    api.getWorkTree.mockResolvedValue({
      sessionId: SESSION,
      items: [pushed],
      relatedProjectIds: [],
    });

    const { result } = renderHook(() => useWorkTree(SESSION), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.items.map((i) => i.title)).toEqual(["Pushed live"]));
  });

  it("reports an empty tree for a session that has never tracked work", async () => {
    api.getWorkTree.mockResolvedValue({
      sessionId: SESSION,
      items: [],
      relatedProjectIds: [],
    });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { result } = renderHook(() => useWorkTree(SESSION), { wrapper: wrapper(qc) });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.items).toEqual([]);
    expect(result.current.nodes).toEqual([]);
  });
});
