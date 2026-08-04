import { useQuery } from "@tanstack/react-query";
import { authenticatedFetch } from "@/lib/identity";

export interface AgentOsFollowUp {
  id: string;
  title: string;
  status: "active" | "paused" | "triaged" | "captured";
  project: string;
  next_action: string;
  source: string;
}

export interface AgentOsContinuity {
  object: "agent_os.continuity";
  goal: string | null;
  now: string | null;
  next: string | null;
  decision_needed: string | null;
  session_map: string | null;
  source_updated_at: string | null;
  follow_up_count: number;
  follow_ups: AgentOsFollowUp[];
}

async function fetchAgentOsContinuity(sessionMap: string): Promise<AgentOsContinuity | null> {
  const params = new URLSearchParams({ session_map: sessionMap });
  const response = await authenticatedFetch(`/v1/agent-os/continuity?${params}`, {
    cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return (await response.json()) as AgentOsContinuity;
}

/** Read the optional Sifututor continuity adapter without making it a core dependency. */
export function useAgentOsContinuity(sessionMap: string | null | undefined) {
  return useQuery({
    queryKey: ["agent-os", "continuity", sessionMap],
    queryFn: () => fetchAgentOsContinuity(sessionMap as string),
    enabled: Boolean(sessionMap),
    staleTime: 30_000,
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
    retry: false,
  });
}
