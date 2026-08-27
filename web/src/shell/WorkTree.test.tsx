// Tests for WorkTree — the durable, provider-neutral task surface in the
// right rail. The API layer is mocked so these exercise the component's own
// behaviour: the six statuses, delivery state kept visually separate from
// status, the three-level limit, accessible expand/collapse, edit, delete
// refusal, project markers, and the loading / empty / conflict / error states.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type * as WorkTreeApiModule from "@/lib/workTreeApi";
import type { WorkItem, WorkStatus } from "@/lib/workTreeApi";

const api = vi.hoisted(() => ({
  getWorkTree: vi.fn(),
  createWorkItem: vi.fn(),
  updateWorkItem: vi.fn(),
  deleteWorkItem: vi.fn(),
  deferWorkItem: vi.fn(),
  resumeWorkItem: vi.fn(),
}));

class FakeApiError extends Error {
  status: number;
  constructor(status: number) {
    super(`status ${status}`);
    this.status = status;
  }
}

vi.mock("@/lib/workTreeApi", async (importOriginal) => {
  const actual = await importOriginal<typeof WorkTreeApiModule>();
  return {
    ...actual,
    getWorkTree: api.getWorkTree,
    createWorkItem: api.createWorkItem,
    updateWorkItem: api.updateWorkItem,
    deleteWorkItem: api.deleteWorkItem,
    deferWorkItem: api.deferWorkItem,
    resumeWorkItem: api.resumeWorkItem,
    isVersionConflict: (e: unknown) => e instanceof FakeApiError && e.status === 409,
  };
});

import { WorkTree } from "./WorkTree";

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

function renderTree(items: WorkItem[], props: { homeProjectId?: string | null } = {}) {
  api.getWorkTree.mockResolvedValue({
    sessionId: SESSION,
    items,
    relatedProjectIds: [],
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <WorkTree sessionId={SESSION} homeProjectId={props.homeProjectId ?? null} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  nextId = 0;
  api.getWorkTree.mockReset();
  api.createWorkItem.mockReset().mockResolvedValue(item());
  api.updateWorkItem.mockReset().mockResolvedValue(item());
  api.deleteWorkItem.mockReset().mockResolvedValue(undefined);
  api.deferWorkItem.mockReset().mockResolvedValue(item());
  api.resumeWorkItem.mockReset().mockResolvedValue(item());
});

afterEach(cleanup);

describe("WorkTree statuses", () => {
  const statuses: WorkStatus[] = ["not_started", "working", "waiting", "paused", "blocked", "done"];

  it.each(statuses)("renders a distinct glyph for %s", async (status) => {
    // WHY: the six statuses must be tellable apart without relying on colour,
    // so each renders its own glyph element.
    renderTree([item({ status, title: `A ${status} item` })]);
    expect(await screen.findByTestId(`work-item-status-${status}`)).toBeInTheDocument();
  });

  it("shows every status in one tree at once", async () => {
    renderTree(statuses.map((status) => item({ status })));
    await screen.findByTestId("work-tree-items");
    for (const status of statuses) {
      expect(screen.getByTestId(`work-item-status-${status}`)).toBeInTheDocument();
    }
  });

  it("shows aggregate progress and keeps each row status readable", async () => {
    renderTree([
      item({ title: "Finished", status: "done" }),
      item({ title: "Blocked step", status: "blocked" }),
    ]);

    expect(
      await screen.findByRole("progressbar", {
        name: "Implementation progress 1 of 2, Blocked",
      }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("implementation-progress")).toHaveTextContent("1/2 · Blocked");
    expect(screen.getByLabelText("Status of Blocked step")).toHaveValue("blocked");
    expect(screen.getByLabelText("Status of Blocked step")).not.toHaveClass("opacity-0");
  });

  it("keeps the empty implementation tree reachable and actionable", async () => {
    renderTree([]);

    expect(await screen.findByText("Implementation tree")).toBeInTheDocument();
    expect(screen.getByText("0/0 · Not started")).toBeInTheDocument();
    expect(await screen.findByTestId("work-tree-empty")).toHaveTextContent(
      "No implementation steps yet",
    );
    expect(screen.getByRole("textbox", { name: "Add work item" })).toBeInTheDocument();
  });
});

describe("WorkTree delivery state", () => {
  it("shows delivery state separately from status", async () => {
    // WHY: a green "done" must never read as shipped. The delivery badge is
    // its own element with its own words.
    const merged = item({ status: "done", deliveryState: "merged", title: "Merged work" });
    renderTree([merged]);
    const badge = await screen.findByTestId(`work-item-delivery-${merged.id}`);
    expect(badge).toHaveTextContent("Merged");
    expect(screen.getByTestId("work-item-status-done")).toBeInTheDocument();
  });

  it("shows no delivery badge when the item has no delivery meaning", async () => {
    // WHY: finishing the work alone must not imply any delivery state at all.
    const done = item({ status: "done", deliveryState: null });
    renderTree([done]);
    await screen.findByTestId("work-tree-items");
    expect(screen.queryByTestId(`work-item-delivery-${done.id}`)).toBeNull();
  });
});

describe("WorkTree nesting", () => {
  it("renders three levels of depth", async () => {
    const root = item({ title: "Programme", depth: 1 });
    const task = item({ title: "Task", depth: 2, parentId: root.id });
    const subtask = item({ title: "Subtask", depth: 3, parentId: task.id });
    renderTree([root, task, subtask]);

    await screen.findByText("Programme");
    // The parent auto-expands (it is not done and not collapsed), so the
    // whole chain is reachable without interaction.
    expect(screen.getByText("Task")).toBeInTheDocument();
    expect(screen.getByText("Subtask")).toBeInTheDocument();
    expect(screen.getByTestId(`work-item-${subtask.id}`)).toHaveAttribute("data-depth", "3");
  });

  it("offers no add-subtask affordance at the deepest level", async () => {
    // WHY: the tree stops at three levels, so the UI must not invite a fourth.
    const root = item({ title: "Programme", depth: 1 });
    const task = item({ title: "Task", depth: 2, parentId: root.id });
    const subtask = item({ title: "Subtask", depth: 3, parentId: task.id });
    renderTree([root, task, subtask]);

    await screen.findByText("Subtask");
    expect(screen.getByLabelText("Add subtask under Task")).toBeInTheDocument();
    expect(screen.queryByLabelText("Add subtask under Subtask")).toBeNull();
  });

  it("collapses and expands a branch through an accessible control", async () => {
    const root = item({ title: "Parent" });
    const child = item({ title: "Child", depth: 2, parentId: root.id });
    renderTree([root, child]);

    await screen.findByText("Child");
    const toggle = screen.getByLabelText("Collapse Parent");
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(toggle);
    expect(screen.queryByText("Child")).toBeNull();
    expect(screen.getByLabelText("Expand Parent")).toHaveAttribute("aria-expanded", "false");
  });

  it("keeps a completed branch present but collapsed", async () => {
    // WHY: finished work should stop competing for attention without
    // disappearing — the user can always reopen it.
    const root = item({ title: "Finished parent", status: "done" });
    const child = item({ title: "Hidden child", depth: 2, parentId: root.id });
    renderTree([root, child]);

    await screen.findByText("Finished parent");
    expect(screen.queryByText("Hidden child")).toBeNull();

    fireEvent.click(screen.getByLabelText("Expand Finished parent"));
    expect(screen.getByText("Hidden child")).toBeInTheDocument();
  });
});

describe("WorkTree details", () => {
  it("reveals why, evidence, delivery, source and next action when expanded", async () => {
    const rich = item({
      title: "Rich item",
      why: "It unblocks review",
      evidence: "Tests pass locally",
      nextAction: "Open the PR",
      deliveryState: "committed",
      sourceKind: "provider_todo",
    });
    renderTree([rich]);

    const details = await screen.findByTestId(`work-item-details-${rich.id}`);
    expect(within(details).getByText("It unblocks review")).toBeInTheDocument();
    expect(within(details).getByText("Tests pass locally")).toBeInTheDocument();
    expect(within(details).getByText("Open the PR")).toBeInTheDocument();
    expect(within(details).getByText("Committed")).toBeInTheDocument();
    expect(within(details).getByText("Agent checklist")).toBeInTheDocument();
  });

  it("marks a project only when it differs from the session's home project", async () => {
    const same = item({ title: "Same project", projectId: "proj_home" });
    const other = item({ title: "Other project", projectId: "proj_other" });
    renderTree([same, other], { homeProjectId: "proj_home" });

    await screen.findByText("Same project");
    expect(screen.queryByTestId(`work-item-project-${same.id}`)).toBeNull();
    expect(screen.getByTestId(`work-item-project-${other.id}`)).toHaveTextContent("proj_other");
  });
});

describe("WorkTree mutations", () => {
  it("creates an item through the durable API", async () => {
    renderTree([]);
    await screen.findByTestId("work-tree-empty");

    fireEvent.change(screen.getByTestId("work-tree-add-input"), {
      target: { value: "New work" },
    });
    fireEvent.click(screen.getByTestId("work-tree-add-button"));

    await waitFor(() =>
      expect(api.createWorkItem).toHaveBeenCalledWith(SESSION, {
        title: "New work",
        parentId: null,
      }),
    );
  });

  it("sends the version the user last saw when changing status", async () => {
    // WHY: the version is what stops one window silently overwriting another.
    const existing = item({ title: "Task", version: 7 });
    renderTree([existing]);
    await screen.findByText("Task");

    fireEvent.change(screen.getByLabelText("Status of Task"), { target: { value: "working" } });

    await waitFor(() =>
      expect(api.updateWorkItem).toHaveBeenCalledWith(SESSION, existing.id, {
        version: 7,
        status: "working",
      }),
    );
  });

  it("renames an item in place", async () => {
    const existing = item({ title: "Old title", version: 3 });
    renderTree([existing]);
    await screen.findByText("Old title");

    fireEvent.click(screen.getByLabelText("Edit Old title"));
    const field = screen.getByLabelText("Rename Old title");
    fireEvent.change(field, { target: { value: "New title" } });
    fireEvent.blur(field);

    await waitFor(() =>
      expect(api.updateWorkItem).toHaveBeenCalledWith(SESSION, existing.id, {
        version: 3,
        title: "New title",
      }),
    );
  });

  it("defers and resumes an item", async () => {
    const active = item({ title: "Active", version: 2 });
    const { rerender } = renderTree([active]);
    await screen.findByText("Active");

    fireEvent.click(screen.getByLabelText("Defer Active"));
    await waitFor(() => expect(api.deferWorkItem).toHaveBeenCalledWith(SESSION, active.id, 2));

    cleanup();
    const parked = item({ title: "Parked", status: "paused", deferredAt: 123, version: 4 });
    renderTree([parked]);
    await screen.findByText("Parked");
    fireEvent.click(screen.getByLabelText("Resume Parked"));
    await waitFor(() => expect(api.resumeWorkItem).toHaveBeenCalledWith(SESSION, parked.id, 4));
    void rerender;
  });

  it("deletes a leaf item", async () => {
    const leaf = item({ title: "Leaf", version: 5 });
    renderTree([leaf]);
    await screen.findByText("Leaf");

    fireEvent.click(screen.getByLabelText("Delete Leaf"));
    await waitFor(() => expect(api.deleteWorkItem).toHaveBeenCalledWith(SESSION, leaf.id, 5));
  });

  it("explains that a parent's subtasks must go first", async () => {
    // WHY: the server refuses to cascade, so the UI has to say what to do
    // rather than showing a bare failure.
    api.deleteWorkItem.mockRejectedValue(new FakeApiError(409));
    const parent = item({ title: "Parent" });
    const child = item({ title: "Child", depth: 2, parentId: parent.id });
    renderTree([parent, child]);
    await screen.findByText("Child");

    fireEvent.click(screen.getByLabelText("Delete Parent"));
    expect(await screen.findByTestId("work-tree-conflict")).toHaveTextContent(
      "Move or delete its subtasks first.",
    );
  });

  it("tells the user when another window already moved an item on", async () => {
    api.updateWorkItem.mockRejectedValue(new FakeApiError(409));
    renderTree([item({ title: "Contested" })]);
    await screen.findByText("Contested");

    fireEvent.change(screen.getByLabelText("Status of Contested"), {
      target: { value: "done" },
    });

    expect(await screen.findByTestId("work-tree-conflict")).toHaveTextContent(
      "This item changed somewhere else.",
    );
  });
});

describe("WorkTree states", () => {
  it("shows a loading state before the first tree arrives", () => {
    api.getWorkTree.mockReturnValue(new Promise(() => {}));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <WorkTree sessionId={SESSION} />
      </QueryClientProvider>,
    );
    expect(screen.getByTestId("work-tree-loading")).toBeInTheDocument();
  });

  it("shows an empty state for a session with no tracked work", async () => {
    renderTree([]);
    expect(await screen.findByTestId("work-tree-empty")).toBeInTheDocument();
  });

  it("offers a retry when the tree cannot be loaded", async () => {
    api.getWorkTree.mockRejectedValue(new Error("network down"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <WorkTree sessionId={SESSION} />
      </QueryClientProvider>,
    );
    const error = await screen.findByTestId("work-tree-error");
    expect(within(error).getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("recovers the current tree when the user reloads after a conflict", async () => {
    // WHY: the conflict banner is only useful if it also gets the user back to
    // the truth — the reload refetches full state.
    api.updateWorkItem.mockRejectedValue(new FakeApiError(409));
    renderTree([item({ title: "Contested" })]);
    await screen.findByText("Contested");

    fireEvent.change(screen.getByLabelText("Status of Contested"), {
      target: { value: "done" },
    });
    const banner = await screen.findByTestId("work-tree-conflict");
    api.getWorkTree.mockClear();

    fireEvent.click(within(banner).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(api.getWorkTree).toHaveBeenCalled());
  });

  it("renders nothing without a session", () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { container } = render(
      <QueryClientProvider client={qc}>
        <WorkTree sessionId={null} />
      </QueryClientProvider>,
    );
    expect(container.firstChild).toBeNull();
  });
});
