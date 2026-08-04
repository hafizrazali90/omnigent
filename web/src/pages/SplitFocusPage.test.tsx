import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SplitFocusPage } from "./SplitFocusPage";
import * as conversationsHook from "@/hooks/useConversations";
import type { Conversation } from "@/hooks/useConversations";

vi.mock("@/hooks/useConversations", () => ({ useConversations: vi.fn() }));

function conversation(id: string, title: string): Conversation {
  return {
    id,
    object: "conversation",
    title,
    created_at: 1_700_000_000,
    updated_at: 1_700_000_100,
    labels: {},
    permission_level: 3,
    workspace: `/workspace/${id}`,
    agent_name: "Codex",
    status: "running",
    pending_elicitations_count: 0,
    runner_online: true,
  };
}

function setSessions(
  rows: Conversation[],
  state: {
    hasNextPage?: boolean;
    fetchNextPage?: () => unknown;
    isFetchingNextPage?: boolean;
  } = {},
) {
  vi.mocked(conversationsHook.useConversations).mockReturnValue({
    data: { pages: [{ data: rows, first_id: null, last_id: null, has_more: false }] },
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    hasNextPage: state.hasNextPage ?? false,
    fetchNextPage: state.fetchNextPage ?? vi.fn(),
    isFetchingNextPage: state.isFetchingNextPage ?? false,
  } as unknown as ReturnType<typeof conversationsHook.useConversations>);
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.pathname + location.search}</output>;
}

function renderPage(entry = "/split-focus") {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route
          path="/split-focus"
          element={
            <>
              <SplitFocusPage />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  setSessions([
    conversation("conv_alpha", "Review payment safeguards"),
    conversation("conv_beta", "Prepare CRM release"),
    conversation("conv_gamma", "Check mobile readiness"),
    conversation("conv_delta", "Write staff guide"),
  ]);
});

afterEach(cleanup);

describe("SplitFocusPage", () => {
  it("opens two complete isolated session workspaces by default", async () => {
    renderPage();

    expect(screen.getByRole("heading", { name: "Split Focus" })).toBeInTheDocument();
    const frames = screen.getAllByTitle(/Task workspace:/);
    expect(frames).toHaveLength(2);
    expect(frames[0]).toHaveAttribute("src", "/c/conv_alpha?split-pane=1");
    expect(frames[1]).toHaveAttribute("src", "/c/conv_beta?split-pane=1");
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/split-focus?session=conv_alpha&session=conv_beta",
      ),
    );
  });

  it("keeps an explicitly selected task and fills the second pane", async () => {
    renderPage("/split-focus?session=conv_gamma");

    const frames = screen.getAllByTitle(/Task workspace:/);
    expect(frames[0]).toHaveAttribute("src", "/c/conv_gamma?split-pane=1");
    expect(frames[1]).toHaveAttribute("src", "/c/conv_alpha?split-pane=1");
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/split-focus?session=conv_gamma&session=conv_alpha",
      ),
    );
  });

  it("adds, changes, and removes panes while persisting selection in the URL", async () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Add workspace" }));
    expect(screen.getAllByTitle(/Task workspace:/)).toHaveLength(3);
    expect(screen.getAllByTitle(/Task workspace:/)[2]).toHaveAttribute(
      "src",
      "/c/conv_gamma?split-pane=1",
    );

    fireEvent.pointerDown(screen.getByRole("combobox", { name: "Workspace 1" }), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    fireEvent.click(screen.getByRole("option", { name: "Write staff guide" }));
    expect(screen.getAllByTitle(/Task workspace:/)[0]).toHaveAttribute(
      "src",
      "/c/conv_delta?split-pane=1",
    );

    fireEvent.click(screen.getByRole("button", { name: "Remove Check mobile readiness" }));
    expect(screen.getAllByTitle(/Task workspace:/)).toHaveLength(2);
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/split-focus?session=conv_delta&session=conv_beta",
      ),
    );
  });

  it("caps the workspace at four panes", () => {
    renderPage();

    fireEvent.click(screen.getByRole("button", { name: "Add workspace" }));
    fireEvent.click(screen.getByRole("button", { name: "Add workspace" }));

    expect(screen.getAllByTitle(/Task workspace:/)).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "Add workspace" })).toBeNull();
  });

  it("searches the task picker and can load older tasks", () => {
    const fetchNextPage = vi.fn();
    setSessions(
      [
        conversation("conv_alpha", "Review payment safeguards"),
        conversation("conv_beta", "Prepare CRM release"),
        conversation("conv_gamma", "Check mobile readiness"),
      ],
      { hasNextPage: true, fetchNextPage },
    );
    renderPage();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search Split Focus tasks" }), {
      target: { value: "mobile" },
    });
    fireEvent.pointerDown(screen.getByRole("combobox", { name: "Workspace 1" }), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    expect(screen.getAllByRole("option", { name: "Check mobile readiness" })).not.toHaveLength(0);
    expect(screen.queryAllByRole("option", { name: "Prepare CRM release" })).toHaveLength(0);
    fireEvent.click(screen.getByRole("option", { name: "Check mobile readiness" }));

    fireEvent.click(screen.getByRole("button", { name: "Load more tasks" }));
    expect(fetchNextPage).toHaveBeenCalledOnce();
  });

  it("shows an actionable empty state when no sessions exist", () => {
    setSessions([]);
    renderPage();

    expect(screen.getByText("No sessions yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New session" })).toHaveAttribute("href", "/");
  });
});
