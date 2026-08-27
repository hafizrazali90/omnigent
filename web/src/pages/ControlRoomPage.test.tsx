import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ControlRoomPage, formatControlRoomModel } from "./ControlRoomPage";
import * as conversationsHook from "@/hooks/useConversations";
import * as controlRoomLaneHook from "@/hooks/useControlRoomLane";
import * as sessionHook from "@/hooks/useSession";
import * as workTreeHook from "@/hooks/useWorkTree";
import type { Conversation } from "@/hooks/useConversations";

vi.mock("@/hooks/useConversations", () => ({ useConversations: vi.fn() }));
vi.mock("@/hooks/useControlRoomLane", () => ({ useControlRoomLane: vi.fn() }));
vi.mock("@/hooks/useSession", () => ({ useSession: vi.fn() }));
vi.mock("@/hooks/useWorkTree", () => ({ useWorkTree: vi.fn() }));

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "conv_1",
    object: "conversation",
    title: "Review payment safeguards",
    created_at: 1_700_000_000,
    updated_at: 1_700_000_100,
    labels: {},
    permission_level: 3,
    workspace: "/Users/hafiz/Projects/sifu-tutor",
    agent_name: "codex-native-ui",
    status: "running",
    pending_elicitations_count: 0,
    runner_online: true,
    ...overrides,
  };
}

function setQuery(
  rows: Conversation[],
  state: {
    isLoading?: boolean;
    isError?: boolean;
    refetch?: () => unknown;
    hasNextPage?: boolean;
    fetchNextPage?: () => unknown;
    isFetchingNextPage?: boolean;
  } = {},
) {
  vi.mocked(conversationsHook.useConversations).mockReturnValue({
    data: { pages: [{ data: rows, first_id: null, last_id: null, has_more: false }] },
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
    refetch: state.refetch ?? vi.fn(),
    hasNextPage: state.hasNextPage ?? false,
    fetchNextPage: state.fetchNextPage ?? vi.fn(),
    isFetchingNextPage: state.isFetchingNextPage ?? false,
  } as unknown as ReturnType<typeof conversationsHook.useConversations>);
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ControlRoomPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  vi.mocked(controlRoomLaneHook.useControlRoomLane).mockReturnValue({
    messages: [],
    isLoadingMessages: false,
    messagesError: null,
    isSending: false,
    sendError: null,
    send: vi.fn(),
  });
  vi.mocked(sessionHook.useSession).mockReturnValue({
    session: {
      modelOverride: "gpt-5.6-sol",
      llmModel: null,
      usageByModel: { "gpt-5.6-sol": {} },
      codexModelOptions: [
        {
          id: "gpt-5.6-sol",
          model: "gpt-5.6-sol",
          displayName: "GPT-5.6 Sol",
          supportedReasoningEfforts: [],
          isDefault: true,
        },
      ],
      pendingElicitations: [],
    },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof sessionHook.useSession>);
  vi.mocked(workTreeHook.useWorkTree).mockReturnValue({
    items: [
      {
        id: "work_1",
        title: "Add the payment regression test",
        nextAction: "Run the focused browser journey",
        status: "working",
        sortOrder: 0,
      },
    ],
  } as unknown as ReturnType<typeof workTreeHook.useWorkTree>);
});

afterEach(() => cleanup());

describe("ControlRoomPage", () => {
  it("formats observed Claude models and omits ambiguous model history", () => {
    expect(
      formatControlRoomModel({
        modelOverride: null,
        llmModel: null,
        usageByModel: { "claude-sonnet-5": {} },
        codexModelOptions: [],
      } as unknown as Parameters<typeof formatControlRoomModel>[0]),
    ).toBe("Claude Sonnet 5");
    expect(
      formatControlRoomModel({
        modelOverride: null,
        llmModel: null,
        usageByModel: { "gpt-5.6-sol": {}, "gpt-5.5": {} },
        codexModelOptions: [],
      } as unknown as Parameters<typeof formatControlRoomModel>[0]),
    ).toBeNull();
    expect(
      formatControlRoomModel({
        modelOverride: "openai/gpt-4o-mini",
        llmModel: null,
        usageByModel: null,
        codexModelOptions: [],
      } as unknown as Parameters<typeof formatControlRoomModel>[0]),
    ).toBe("GPT-4o Mini");
  });

  it("renders real session metadata as parallel task lanes", () => {
    setQuery([
      conversation(),
      conversation({
        id: "conv_2",
        title: "CRM release review",
        status: "idle",
        agent_name: "Claude",
        pending_elicitations_count: 1,
        workspace: "/Users/hafiz/Projects/ripple-suite",
      }),
    ]);

    renderPage();

    expect(screen.getByRole("heading", { name: "Control Room" })).toBeInTheDocument();
    const lanes = screen.getAllByTestId("control-room-lane");
    expect(lanes).toHaveLength(2);
    expect(within(lanes[0]).getByText("Review payment safeguards")).toBeInTheDocument();
    expect(within(lanes[0]).getByText("sifu-tutor")).toBeInTheDocument();
    expect(within(lanes[0]).getByText("GPT-5.6 Sol")).toBeInTheDocument();
    expect(within(lanes[0]).queryByText("codex-native-ui")).toBeNull();
    expect(within(lanes[0]).getByText("Working")).toBeInTheDocument();
    expect(within(lanes[1]).getByText("Needs you")).toBeInTheDocument();
  });

  it("defaults to bounded compact lanes with a scan-first hierarchy and honest columns", () => {
    setQuery([conversation()]);

    renderPage();

    const lanes = screen.getByTestId("control-room-lanes");
    const lane = screen.getByTestId("control-room-lane");
    expect(lanes).toHaveAttribute("data-density", "compact");
    expect(lanes).toHaveStyle({ gridTemplateColumns: "repeat(4, minmax(17rem, 1fr))" });
    expect(lane).toHaveClass("h-[30rem]");
    expect(within(lane).getByText("Working on")).toBeInTheDocument();
    expect(within(lane).getByText("Add the payment regression test")).toBeInTheDocument();
    expect(within(lane).queryByText("Now")).toBeNull();
    expect(within(lane).queryByText("Goal")).toBeNull();
    expect(within(lane).queryByText("Proof")).toBeNull();
    expect(within(lane).queryByText("Current session")).toBeNull();
    expect(within(lane).queryByText("Latest run stopped and needs review.")).toBeNull();
    expect(within(lane).getByTestId("control-room-lane-transcript")).toHaveClass("overflow-y-auto");
    expect(
      within(lane).getByRole("textbox", { name: "Reply to Review payment safeguards" }),
    ).toHaveAttribute("rows", "1");
    expect(
      within(lane).getByRole("textbox", { name: "Reply to Review payment safeguards" }),
    ).toHaveClass("min-h-10");
    expect(within(lane).getByRole("button", { name: "Send reply" })).toHaveClass(
      "size-10",
      "md:size-10",
    );
  });

  it("keeps the task title primary and omits label-derived goal and proof metadata", () => {
    setQuery([
      conversation({
        status: "idle",
        labels: {
          "agent_os.project": "ripple-suite",
          "agent_os.workflow": "review",
          "agent_os.finish_line": "PR opened",
          "agent_os.route_status": "confirmed",
          "agent_os.route_source": "orchestrator",
          "agent_os.proven_state": "Pushed branch; no PR",
          "agent_os.proven_state_evidence": "Remote SHA matches local",
        },
      }),
    ]);

    renderPage();

    const lane = screen.getByTestId("control-room-lane");
    expect(within(lane).getByText("Review payment safeguards")).toBeInTheDocument();
    expect(within(lane).queryByText("PR opened")).toBeNull();
    expect(within(lane).queryByText("Pushed branch; no PR")).toBeNull();
    expect(within(lane).queryByText("Remote SHA matches local")).toBeNull();
    expect(within(lane).getByText("Idle")).toBeInTheDocument();
    expect(within(lane).queryByText("Ready")).toBeNull();
  });

  it("prioritizes a real pending question over the work tree", () => {
    setQuery([conversation({ pending_elicitations_count: 1 })]);
    vi.mocked(sessionHook.useSession).mockReturnValue({
      session: {
        modelOverride: "gpt-5.6-sol",
        codexModelOptions: [],
        usageByModel: { "gpt-5.6-sol": {} },
        pendingElicitations: [
          {
            elicitation_id: "eli_1",
            params: { message: "Approve the production smoke?", mode: "form" },
          },
        ],
      },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof sessionHook.useSession>);

    renderPage();

    const lane = screen.getByTestId("control-room-lane");
    expect(within(lane).getByText("Waiting for you")).toBeInTheDocument();
    expect(within(lane).getByText("Approve the production smoke?")).toBeInTheDocument();
    expect(within(lane).queryByText("Add the payment regression test")).toBeNull();
  });

  it("omits the task-state line when no honest structured detail exists", () => {
    setQuery([conversation({ status: "failed" })]);
    vi.mocked(workTreeHook.useWorkTree).mockReturnValue({
      items: [],
    } as unknown as ReturnType<typeof workTreeHook.useWorkTree>);

    renderPage();

    const lane = screen.getByTestId("control-room-lane");
    expect(within(lane).getByText("Needs review")).toBeInTheDocument();
    expect(within(lane).queryByTestId("control-room-task-state")).toBeNull();
  });

  it("searches loaded tasks and can fetch older tasks explicitly", () => {
    const fetchNextPage = vi.fn();
    setQuery(
      [
        conversation(),
        conversation({ id: "conv_2", title: "Prepare CRM release", status: "idle" }),
      ],
      { hasNextPage: true, fetchNextPage },
    );

    renderPage();

    fireEvent.change(screen.getByRole("searchbox", { name: "Search tasks" }), {
      target: { value: "CRM" },
    });
    expect(screen.getAllByTestId("control-room-lane")).toHaveLength(1);
    expect(screen.getByText("Prepare CRM release")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Load more tasks" }));
    expect(fetchNextPage).toHaveBeenCalledOnce();
  });

  it("changes layout density and preserves manual task ordering", () => {
    setQuery([
      conversation(),
      conversation({ id: "conv_2", title: "Prepare CRM release", status: "idle" }),
    ]);

    renderPage();

    fireEvent.pointerDown(screen.getByRole("combobox", { name: "Visible columns" }), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    fireEvent.click(screen.getByRole("option", { name: "2" }));
    fireEvent.pointerDown(screen.getByRole("combobox", { name: "Density" }), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    fireEvent.click(screen.getByRole("option", { name: "Comfortable" }));
    expect(screen.getByTestId("control-room-lanes")).toHaveAttribute("data-columns", "2");
    expect(screen.getByTestId("control-room-lanes")).toHaveAttribute("data-density", "comfortable");

    const crmLane = screen.getAllByTestId("control-room-lane")[1];
    fireEvent.pointerDown(within(crmLane).getByRole("button", { name: "More task actions" }), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    fireEvent.click(screen.getByRole("menuitem", { name: "Move task left" }));
    const lanes = screen.getAllByTestId("control-room-lane");
    expect(within(lanes[0]).getByText("Prepare CRM release")).toBeInTheDocument();
    expect(window.localStorage.getItem("agent-os.control-room.order")).toContain("conv_2");
  });

  it("opens a task in Omnigent's existing session workspace", () => {
    setQuery([conversation()]);
    renderPage();

    const header = screen.getByTestId("control-room-lane-header");
    expect(
      within(header).getByRole("link", { name: "Open task: Review payment safeguards" }),
    ).toHaveAttribute("href", "/c/conv_1");
    expect(screen.getAllByRole("link", { name: /Open task/i })).toHaveLength(1);

    fireEvent.pointerDown(screen.getByRole("button", { name: "More task actions" }), {
      button: 0,
      ctrlKey: false,
      pointerType: "mouse",
    });
    expect(screen.getByRole("menuitem", { name: /Open in Split Focus/i })).toHaveAttribute(
      "href",
      "/split-focus?session=conv_1",
    );
    expect(screen.getByRole("menuitem", { name: "Move task left" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByRole("menuitem", { name: "Move task right" })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("shows each lane's own transcript and sends only through that lane", async () => {
    const sendAlpha = vi.fn().mockResolvedValue(undefined);
    const sendBeta = vi.fn().mockResolvedValue(undefined);
    vi.mocked(controlRoomLaneHook.useControlRoomLane).mockImplementation((sessionId) => ({
      messages: [
        {
          id: `${sessionId}_message`,
          role: "assistant",
          text: sessionId === "conv_1" ? "Alpha transcript" : "Beta transcript",
        },
      ],
      isLoadingMessages: false,
      messagesError: null,
      isSending: false,
      sendError: null,
      send: sessionId === "conv_1" ? sendAlpha : sendBeta,
    }));
    setQuery([
      conversation(),
      conversation({ id: "conv_2", title: "CRM release review", status: "idle" }),
    ]);

    renderPage();

    const lanes = screen.getAllByTestId("control-room-lane");
    expect(within(lanes[0]).getByText("Alpha transcript")).toBeInTheDocument();
    expect(within(lanes[1]).getByText("Beta transcript")).toBeInTheDocument();

    fireEvent.change(
      within(lanes[0]).getByRole("textbox", { name: "Reply to Review payment safeguards" }),
      { target: { value: "Continue only alpha" } },
    );
    fireEvent.click(within(lanes[0]).getByRole("button", { name: "Send reply" }));

    expect(sendAlpha).toHaveBeenCalledWith("Continue only alpha");
    expect(sendBeta).not.toHaveBeenCalled();
  });

  it("shows the loading state", () => {
    setQuery([], { isLoading: true });
    renderPage();
    expect(screen.getByText("Loading sessions…")).toBeInTheDocument();
  });

  it("shows an actionable load error", () => {
    const refetch = vi.fn();
    setQuery([], { isError: true, refetch });
    renderPage();
    screen.getByRole("button", { name: "Retry" }).click();
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t load sessions.");
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("shows a useful empty state", () => {
    setQuery([]);
    renderPage();
    expect(screen.getByText("No sessions yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New session" })).toHaveAttribute("href", "/");
  });
});
