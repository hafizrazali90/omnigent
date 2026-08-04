export const AGENT_OS_PROJECT_LABEL = "agent_os.project";
export const AGENT_OS_WORKFLOW_LABEL = "agent_os.workflow";
export const AGENT_OS_FINISH_LINE_LABEL = "agent_os.finish_line";
export const AGENT_OS_FINISH_LINE_SOURCE_LABEL = "agent_os.finish_line_source";
export const AGENT_OS_ROUTE_STATUS_LABEL = "agent_os.route_status";
export const AGENT_OS_ROUTE_QUESTION_LABEL = "agent_os.route_question";
export const AGENT_OS_ROUTE_SOURCE_LABEL = "agent_os.route_source";

export interface AgentOsTaskUnderstanding {
  project: string;
  workflow: string;
  finishLine: string;
  status: string;
  question: string;
  source: string;
}

export function agentOsUnderstandingFromLabels(
  labels: Record<string, string> | null | undefined,
): AgentOsTaskUnderstanding | null {
  const project = labels?.[AGENT_OS_PROJECT_LABEL]?.trim();
  const workflow = labels?.[AGENT_OS_WORKFLOW_LABEL]?.trim();
  if (!project || !workflow) return null;
  return {
    project,
    workflow,
    finishLine: labels?.[AGENT_OS_FINISH_LINE_LABEL]?.trim() || "",
    status: labels?.[AGENT_OS_ROUTE_STATUS_LABEL]?.trim() || "understood",
    question: labels?.[AGENT_OS_ROUTE_QUESTION_LABEL]?.trim() || "",
    source: labels?.[AGENT_OS_ROUTE_SOURCE_LABEL]?.trim() || "orchestrator",
  };
}

export function correctedUnderstandingLabels(
  project: string,
  workflow: string,
  finishLine: string,
): Record<string, string> {
  return {
    [AGENT_OS_PROJECT_LABEL]: project.trim(),
    [AGENT_OS_WORKFLOW_LABEL]: workflow.trim(),
    [AGENT_OS_FINISH_LINE_LABEL]: finishLine.trim(),
    [AGENT_OS_FINISH_LINE_SOURCE_LABEL]: "user-corrected",
    [AGENT_OS_ROUTE_STATUS_LABEL]: "confirmed",
    [AGENT_OS_ROUTE_QUESTION_LABEL]: "",
    [AGENT_OS_ROUTE_SOURCE_LABEL]: "user-corrected",
  };
}
