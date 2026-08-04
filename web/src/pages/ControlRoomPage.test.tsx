import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ControlRoomPage } from "./ControlRoomPage";
import * as conversationsHook from "@/hooks/useConversations";
import * as controlRoomLaneHook from "@/hooks/useControlRoomLane";
import type { Conversation } from "@/hooks/useConversations";

vi.mock("@/hooks/useConversations", () => ({ useConversations: vi.fn() }));
vi.mock("@/hooks/useControlRoomLane", () => ({ useControlRoomLane: vi.fn() }));

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
    agent_name: "Codex",
    status: "running",
    pending_elicitations_count: 0,
    runner_online: true,
    ...overrides,
  };
}

function setQuery(
  rows: Conversation[],
  state: { isLoading?: boolean; isError?: boolean; refetch?: () => unknown } = {},
) {
  vi.mocked(conversationsHook.useConversations).mockReturnValue({
    data: { pages: [{ data: rows, first_id: null, last_id: null, has_more: false }] },
    isLoading: state.isLoading ?? false,
    isError: state.isError ?? false,
    refetch: state.refetch ?? vi.fn(),
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
  vi.mocked(controlRoomLaneHook.useControlRoomLane).mockReturnValue({
    messages: [],
    isLoadingMessages: false,
    messagesError: null,
    isSending: false,
    sendError: null,
    send: vi.fn(),
  });
});

afterEach(() => cleanup());

describe("ControlRoomPage", () => {
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
    expect(within(lanes[0]).getByText("Codex")).toBeInTheDocument();
    expect(within(lanes[0]).getByText("Working")).toBeInTheDocument();
    expect(within(lanes[1]).getByText("Needs you")).toBeInTheDocument();
  });

  it("opens a task in Omnigent's existing session workspace", () => {
    setQuery([conversation()]);
    renderPage();

    expect(screen.getByRole("link", { name: /Open task/i })).toHaveAttribute("href", "/c/conv_1");
    expect(screen.getByRole("link", { name: /Open in Split Focus/i })).toHaveAttribute(
      "href",
      "/split-focus?session=conv_1",
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
    expect(screen.getByText("Loading your active work…")).toBeInTheDocument();
  });

  it("shows an actionable load error", () => {
    const refetch = vi.fn();
    setQuery([], { isError: true, refetch });
    renderPage();
    screen.getByRole("button", { name: "Try again" }).click();
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn’t load your sessions");
    expect(refetch).toHaveBeenCalledOnce();
  });

  it("shows a useful empty state", () => {
    setQuery([]);
    renderPage();
    expect(screen.getByText("No active tasks yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Start a session" })).toHaveAttribute("href", "/");
  });
});
