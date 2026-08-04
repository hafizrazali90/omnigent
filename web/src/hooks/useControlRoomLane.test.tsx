import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useControlRoomLane } from "./useControlRoomLane";
import * as sessionsApi from "@/lib/sessionsApi";
import type { ConversationItem } from "@/lib/conversationItems";

vi.mock("@/lib/sessionsApi", async (importOriginal) => {
  const actual = await importOriginal<typeof sessionsApi>();
  return {
    ...actual,
    fetchSessionItemsPage: vi.fn(),
    postEvent: vi.fn(),
  };
});

function message(id: string, role: "user" | "assistant", text: string): ConversationItem {
  return {
    id,
    type: "message",
    role,
    content: [{ type: role === "user" ? "input_text" : "output_text", text }],
    response_id: `resp_${id}`,
    status: "completed",
  };
}

function wrapper(client: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children);
}

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("useControlRoomLane", () => {
  it("keeps transcript and send state isolated by session id", async () => {
    vi.mocked(sessionsApi.fetchSessionItemsPage).mockImplementation(async (sessionId) => ({
      items:
        sessionId === "conv_a"
          ? [message("a_1", "assistant", "Alpha transcript")]
          : [message("b_1", "assistant", "Beta transcript")],
      hasMore: false,
    }));
    vi.mocked(sessionsApi.postEvent).mockResolvedValue({ queued: true });
    const queryClient = createQueryClient();

    const laneA = renderHook(() => useControlRoomLane("conv_a"), {
      wrapper: wrapper(queryClient),
    });
    const laneB = renderHook(() => useControlRoomLane("conv_b"), {
      wrapper: wrapper(queryClient),
    });

    await waitFor(() => {
      expect(laneA.result.current.messages[0]?.text).toBe("Alpha transcript");
      expect(laneB.result.current.messages[0]?.text).toBe("Beta transcript");
    });

    await act(async () => {
      await Promise.all([
        laneA.result.current.send("Reply only to alpha"),
        laneB.result.current.send("Reply only to beta"),
      ]);
    });

    expect(sessionsApi.postEvent).toHaveBeenCalledWith("conv_a", {
      type: "message",
      data: {
        role: "user",
        content: [{ type: "input_text", text: "Reply only to alpha" }],
      },
    });
    expect(sessionsApi.postEvent).toHaveBeenCalledWith("conv_b", {
      type: "message",
      data: {
        role: "user",
        content: [{ type: "input_text", text: "Reply only to beta" }],
      },
    });
  });

  it("does not leak one lane's send failure into another lane", async () => {
    vi.mocked(sessionsApi.fetchSessionItemsPage).mockResolvedValue({
      items: [],
      hasMore: false,
    });
    vi.mocked(sessionsApi.postEvent).mockImplementation(async (sessionId) => {
      if (sessionId === "conv_a") throw new Error("Alpha runner unavailable");
      return { queued: true };
    });
    const queryClient = createQueryClient();

    const laneA = renderHook(() => useControlRoomLane("conv_a"), {
      wrapper: wrapper(queryClient),
    });
    const laneB = renderHook(() => useControlRoomLane("conv_b"), {
      wrapper: wrapper(queryClient),
    });

    await act(async () => {
      await Promise.allSettled([
        laneA.result.current.send("Alpha message"),
        laneB.result.current.send("Beta message"),
      ]);
    });

    await waitFor(() => {
      expect(laneA.result.current.sendError?.message).toBe("Alpha runner unavailable");
      expect(laneB.result.current.sendError).toBeNull();
    });
  });

  it("keeps a native pending reply visible only in its own lane", async () => {
    vi.mocked(sessionsApi.fetchSessionItemsPage).mockResolvedValue({
      items: [],
      hasMore: false,
    });
    vi.mocked(sessionsApi.postEvent).mockResolvedValue({
      queued: true,
      pendingId: "pending_native",
    });
    const queryClient = createQueryClient();

    const laneA = renderHook(() => useControlRoomLane("conv_a"), {
      wrapper: wrapper(queryClient),
    });
    const laneB = renderHook(() => useControlRoomLane("conv_b"), {
      wrapper: wrapper(queryClient),
    });

    await act(async () => {
      await laneA.result.current.send("Native reply still pending");
    });

    expect(laneA.result.current.messages).toContainEqual(
      expect.objectContaining({
        role: "user",
        text: "Native reply still pending",
        pending: true,
      }),
    );
    expect(laneB.result.current.messages).toEqual([]);
  });
});
