import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useAgentOsContinuity } from "./useAgentOsContinuity";

function wrapper({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })
      }
    >
      {children}
    </QueryClientProvider>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAgentOsContinuity", () => {
  it("reads the optional continuity summary", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          object: "agent_os.continuity",
          goal: "One place for development work",
          now: "Building continuity",
          next: "Verify the bridge",
          decision_needed: "No",
          session_map: ".agent-os/session-maps/current.md",
          follow_up_count: 1,
          follow_ups: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useAgentOsContinuity(".agent-os/session-maps/current.md"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.now).toBe("Building continuity");
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/agent-os/continuity?session_map=.agent-os%2Fsession-maps%2Fcurrent.md",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("treats an absent adapter as an unavailable optional capability", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 404, statusText: "Not Found" })),
    );

    const { result } = renderHook(() => useAgentOsContinuity(".agent-os/session-maps/current.md"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("does not request continuity until the task owns a Session Map pointer", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useAgentOsContinuity(null), { wrapper });

    expect(result.current.fetchStatus).toBe("idle");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
