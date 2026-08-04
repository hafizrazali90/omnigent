// Subagents tab content for the right-side rail. Renders the session
// tree under the root conversation — a "main" link back to the root,
// then its sub-agent sessions recursively (children, grandchildren,
// …) down to ``MAX_TREE_DEPTH`` levels, each level indented one step
// further. The user can move between any agents in the tree without
// leaving the rail.
//
// The active session may itself be a descendant (the user clicked
// into a sub-agent). The rail still renders the tree from the
// top-level root, with the active row highlighted. AppShell resolves
// the root id (walking the parent chain) and passes it as
// ``rootSessionId``.
//
// Each row is a Link to the target conversation page so cmd/middle-
// click opens it in a new tab, matching the sidebar's behavior.

import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  ActivityIcon,
  BookOpenIcon,
  BotIcon,
  CircleAlertIcon,
  Code2Icon,
  CompassIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CornerDownRightIcon,
  FileTextIcon,
  FlaskConicalIcon,
  GitCompareArrowsIcon,
  GoalIcon,
  ListIcon,
  ListTodoIcon,
  NetworkIcon,
  PlusIcon,
  PencilIcon,
  ScanSearchIcon,
  SearchIcon,
  ShieldCheckIcon,
  UsersIcon,
} from "lucide-react";
import { Link, useLocation } from "@/lib/routing";
import { Badge } from "@/components/ui/badge";
import { AntigravityIcon } from "@/components/icons/AntigravityIcon";
import { ClaudeIcon } from "@/components/icons/ClaudeIcon";
import { CodexIcon } from "@/components/icons/CodexIcon";
import { CursorIcon } from "@/components/icons/CursorIcon";
import { GooseIcon } from "@/components/icons/GooseIcon";
import { HermesIcon } from "@/components/icons/HermesIcon";
import { KimiIcon } from "@/components/icons/KimiIcon";
import { KiroIcon } from "@/components/icons/KiroIcon";
import { NessieIcon } from "@/components/icons/NessieIcon";
import { OpenCodeIcon } from "@/components/icons/OpenCodeIcon";
import { OttoIcon } from "@/components/icons/OttoIcon";
import { PiIcon } from "@/components/icons/PiIcon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RunningDot } from "@/components/RunningDot";
import { MAX_TREE_DEPTH, useChildSessions, type ChildSessionInfo } from "@/hooks/useChildSessions";
import { useSession } from "@/hooks/useSession";
import { useAgentOsContinuity, type AgentOsContinuity } from "@/hooks/useAgentOsContinuity";
import {
  agentOsUnderstandingFromLabels,
  correctedUnderstandingLabels,
  type AgentOsTaskUnderstanding,
} from "@/lib/agentOsUnderstanding";
import { postEvent, updateSession } from "@/lib/sessionsApi";
import type { Session, SessionItem } from "@/lib/types";
import { cn } from "@/lib/utils";

const SubagentsGraphView = lazy(() =>
  import("./SubagentsGraphView").then((m) => ({ default: m.SubagentsGraphView })),
);
import { nativeCodingAgentForWrapper, WRAPPER_LABEL_KEY } from "@/lib/nativeCodingAgents";
import {
  activityDotClassName,
  childStatus,
  sessionStatus,
  type AgentActivity,
  type AgentStatus,
} from "./subagentStatus";
import { AddAgentDialog } from "./AddAgentDialog";

// Session-scoped URL params that the file viewer / Files panel write
// for one session and AppShell's restore effect re-reads on the next.
// Stripping these on rail navigation prevents a sticky ``?file=`` from
// the previous session yanking the user into the file viewer of the
// next one. Other params (e.g. ``?debug=1`` for ``useDebugMode``) are
// global and must be preserved across navigation.
const SESSION_SCOPED_PARAMS = ["file", "diff", "comment", "view"] as const;
const CODEX_NATIVE_SUBAGENT_WRAPPER = "codex-native-ui-subagent";
const OPENCODE_NATIVE_SUBAGENT_WRAPPER = "opencode-native-ui-subagent";
// Pi children are scaffold (no wrapper label); the spawn title's agent-type head (``tool``) is the signal.
const PI_AGENT_NAME = "pi";
type AgentRowIcon = ComponentType<SVGProps<SVGSVGElement>>;

/**
 * Build a rail-link search string from the current URL, dropping the
 * session-scoped params and keeping anything else.
 *
 * @param search - The current ``location.search`` string,
 *   e.g. ``"?file=foo.txt&debug=1"``.
 * @returns A search string suitable for a ``<Link to={{ search }}>``,
 *   e.g. ``"?debug=1"`` or ``""`` when nothing remains.
 */
function railLinkSearch(search: string): string {
  const params = new URLSearchParams(search);
  for (const key of SESSION_SCOPED_PARAMS) params.delete(key);
  const next = params.toString();
  return next ? `?${next}` : "";
}

interface SubagentsPanelProps {
  /** The conversation currently rendered in main. Used only to
   *  highlight the active row. */
  conversationId: string;
  /** Root (parent) session whose children populate the list. When the
   *  user is on a top-level session this is the active id; when on a
   *  child it is the child's parent id. AppShell resolves this from
   *  ``activeSession.parentSessionId``. */
  rootSessionId: string;
  /** Real changed-file count for the focused session's workspace. */
  changedCount?: number;
}

type ViewMode = "list" | "graph";

export function SubagentsPanel({
  conversationId,
  rootSessionId,
  changedCount = 0,
}: SubagentsPanelProps) {
  const { children, isLoading, error } = useChildSessions(rootSessionId);
  const { session: focusedSession } = useSession(conversationId);
  const { session: rootSession } = useSession(rootSessionId);
  const [addOpen, setAddOpen] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [collapsedRows, setCollapsedRows] = useState<Record<string, boolean>>({});
  const toggleCollapsedRow = (id: string) => {
    setCollapsedRows((current) => ({ ...current, [id]: !current[id] }));
  };

  // Loading/error states only surface when there's no cached data to
  // show alongside the "main" row.
  if (isLoading && children.length === 0) {
    return (
      <div className="flex h-full flex-1 items-center justify-center px-4 py-8 text-center text-xs text-muted-foreground bg-card">
        Loading…
      </div>
    );
  }
  if (error && children.length === 0) {
    return (
      <div className="flex h-full flex-1 items-center justify-center px-4 py-8 text-center text-xs text-muted-foreground bg-card">
        Failed to load agents.
      </div>
    );
  }

  if (viewMode === "graph") {
    return (
      <div className="flex h-full min-h-0 flex-col overflow-hidden bg-card">
        <WorkerSidebarSummary
          session={focusedSession}
          rootSession={rootSession}
          directWorkers={children}
          changedCount={changedCount}
        />
        <ViewModeToggle viewMode={viewMode} onViewModeChange={setViewMode} />
        <Suspense
          fallback={
            <div className="flex h-full flex-1 items-center justify-center text-xs text-muted-foreground">
              Loading graph…
            </div>
          }
        >
          <SubagentsGraphView conversationId={conversationId} rootSessionId={rootSessionId} />
        </Suspense>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-card">
      <WorkerSidebarSummary
        session={focusedSession}
        rootSession={rootSession}
        directWorkers={children}
        changedCount={changedCount}
      />
      <ViewModeToggle viewMode={viewMode} onViewModeChange={setViewMode} />
      <button
        type="button"
        data-testid="add-agent-button"
        onClick={() => setAddOpen(true)}
        className="hidden"
      >
        <PlusIcon className="size-3.5 shrink-0" />
        Add agent
      </button>
      <ul className="flex min-h-0 flex-1 flex-col overflow-y-auto pb-1">
        <MainRow rootSessionId={rootSessionId} isActive={conversationId === rootSessionId} />
        {children.map((child) => (
          <SubagentRow
            key={child.id}
            child={child}
            depth={1}
            conversationId={conversationId}
            collapsedRows={collapsedRows}
            onToggleCollapsed={toggleCollapsedRow}
          />
        ))}
      </ul>
      {/* Mounted only while open so a closed rail issues no /v1/agents
          fetch and carries none of the dialog's query dependencies. */}
      {addOpen && (
        <AddAgentDialog parentSessionId={rootSessionId} open={addOpen} onOpenChange={setAddOpen} />
      )}
    </div>
  );
}

function WorkerSidebarSummary({
  session,
  rootSession,
  directWorkers,
  changedCount,
}: {
  session: ReturnType<typeof useSession>["session"];
  rootSession: ReturnType<typeof useSession>["session"];
  directWorkers: ChildSessionInfo[];
  changedCount: number;
}) {
  const { data: continuity } = useAgentOsContinuity(session?.labels?.["agent_os.session_map"]);
  const nativeAgent = nativeCodingAgentForWrapper(session?.labels?.[WRAPPER_LABEL_KEY]);
  const workerName =
    nativeAgent?.displayName ?? session?.subAgentName ?? session?.agentName ?? "Agent";
  const focusedStatus =
    (session?.pendingElicitations?.length ?? 0) > 0
      ? "Needs response"
      : sessionStatus(session?.status).label;
  const todos = session?.todos ?? [];
  const completedTodos = todos.filter((todo) => todo.status === "completed").length;
  const needsYou =
    (rootSession?.pendingElicitations?.length ?? 0) +
    directWorkers.reduce((total, worker) => total + worker.pending_elicitations_count, 0);
  const workerCount = directWorkers.length + 1;
  const taskTitle = session?.title?.trim() || "Untitled task";

  return (
    <section
      data-testid="worker-sidebar-summary"
      className="shrink-0 space-y-3 border-b border-border px-3 py-3"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Current task
          </p>
          <h2 className="truncate text-sm font-semibold">{taskTitle}</h2>
          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
            <ActivityIcon className="size-3.5" />
            <span className="truncate">{workerName}</span>
          </p>
        </div>
        <Badge
          variant="outline"
          className={cn(
            "shrink-0",
            focusedStatus === "Needs response" && "border-warning/30 text-warning",
            focusedStatus === "Failed" && "border-destructive/30 text-destructive",
          )}
        >
          {focusedStatus}
        </Badge>
      </div>

      <div className="grid grid-cols-2 gap-1.5 text-[11px]">
        <WorkerEvidence
          icon={UsersIcon}
          text={`${workerCount} ${workerCount === 1 ? "worker" : "workers"}`}
        />
        <WorkerEvidence
          icon={GitCompareArrowsIcon}
          text={`${changedCount} changed ${changedCount === 1 ? "file" : "files"}`}
        />
        <WorkerEvidence
          icon={ListTodoIcon}
          text={todos.length > 0 ? `${completedTodos}/${todos.length} tasks` : "No task checklist"}
        />
        <WorkerEvidence
          icon={needsYou > 0 ? CircleAlertIcon : ShieldCheckIcon}
          text={needsYou > 0 ? `${needsYou} need you` : "No approvals waiting"}
          attention={needsYou > 0}
        />
      </div>

      <div className="rounded-md border border-border bg-muted/45 px-2.5 py-2">
        <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
          Approval boundary
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-foreground/80">
          Commit, push, and deploy still require your approval.
        </p>
      </div>

      {continuity && <ContinuitySummary continuity={continuity} />}
      <TaskUnderstandingSummary session={rootSession ?? session} />

      <div className="flex items-center justify-between">
        <p className="text-xs font-medium">Workers</p>
        <span className="text-[10px] text-muted-foreground">Live session tree</span>
      </div>
    </section>
  );
}

function TaskUnderstandingSummary({ session }: { session: Session | null }) {
  const understanding = agentOsUnderstandingFromLabels(session?.labels);
  if (!session || !understanding) return null;
  return (
    <TaskUnderstandingCard
      key={session.id}
      sessionId={session.id}
      initialUnderstanding={understanding}
    />
  );
}

function TaskUnderstandingCard({
  sessionId,
  initialUnderstanding,
}: {
  sessionId: string;
  initialUnderstanding: AgentOsTaskUnderstanding;
}) {
  const queryClient = useQueryClient();
  const {
    project: initialProject,
    workflow: initialWorkflow,
    status: initialStatus,
    question: initialQuestion,
    source: initialSource,
  } = initialUnderstanding;
  const initialSignature = [
    initialProject,
    initialWorkflow,
    initialStatus,
    initialQuestion,
    initialSource,
  ].join("\u0000");
  const lastInitialSignature = useRef(initialSignature);
  const [understanding, setUnderstanding] =
    useState<AgentOsTaskUnderstanding>(initialUnderstanding);
  const [editing, setEditing] = useState(false);
  const [project, setProject] = useState(initialUnderstanding.project);
  const [workflow, setWorkflow] = useState(initialUnderstanding.workflow);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (initialSignature === lastInitialSignature.current) return;
    if (editing || saving) return;
    lastInitialSignature.current = initialSignature;
    setUnderstanding({
      project: initialProject,
      workflow: initialWorkflow,
      status: initialStatus,
      question: initialQuestion,
      source: initialSource,
    });
    setProject(initialProject);
    setWorkflow(initialWorkflow);
  }, [
    editing,
    initialProject,
    initialQuestion,
    initialSignature,
    initialSource,
    initialStatus,
    initialWorkflow,
    saving,
  ]);

  async function saveCorrection() {
    const nextProject = project.trim();
    const nextWorkflow = workflow.trim();
    if (!nextProject || !nextWorkflow) {
      setError("Project and workflow are both required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const labels = correctedUnderstandingLabels(nextProject, nextWorkflow);
      const updatedSession = await updateSession(sessionId, { labels });
      queryClient.setQueryData(["session", sessionId], updatedSession);
      setUnderstanding({
        project: nextProject,
        workflow: nextWorkflow,
        status: "confirmed",
        question: "",
        source: "user-corrected",
      });
      setEditing(false);
      try {
        await postEvent(sessionId, {
          type: "message",
          data: {
            role: "user",
            content: [
              {
                type: "input_text",
                text:
                  `[Agent OS route correction] Project: ${nextProject}. ` +
                  `Workflow: ${nextWorkflow}. Use this corrected route for the current task.`,
              },
            ],
          },
        });
      } catch {
        setError("Correction saved, but the worker could not be notified yet.");
      }
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "Could not save the correction.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section
      data-testid="agent-os-task-understanding"
      className={cn(
        "rounded-md border bg-background/55 px-2.5 py-2",
        understanding.status === "needs-clarification"
          ? "border-warning/30 bg-warning/5"
          : "border-border",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
          I understand this as
        </p>
        {!editing && (
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label="Correct understanding"
            title="Correct understanding"
            onClick={() => setEditing(true)}
          >
            <PencilIcon className="size-3.5" />
          </Button>
        )}
      </div>

      {editing ? (
        <div className="mt-2 space-y-2">
          <label className="block text-[10px] font-medium text-muted-foreground">
            Project
            <Input
              value={project}
              maxLength={120}
              onChange={(event) => setProject(event.target.value)}
              className="mt-1 h-7 text-xs"
            />
          </label>
          <label className="block text-[10px] font-medium text-muted-foreground">
            Workflow
            <Input
              value={workflow}
              maxLength={120}
              onChange={(event) => setWorkflow(event.target.value)}
              className="mt-1 h-7 text-xs"
            />
          </label>
          {error && <p className="text-[10px] text-destructive">{error}</p>}
          <div className="flex justify-end gap-1">
            <Button
              type="button"
              variant="ghost"
              size="xs"
              disabled={saving}
              onClick={() => {
                setProject(understanding.project);
                setWorkflow(understanding.workflow);
                setEditing(false);
                setError(null);
              }}
            >
              Cancel
            </Button>
            <Button type="button" size="xs" disabled={saving} onClick={() => void saveCorrection()}>
              {saving ? "Saving…" : "Save correction"}
            </Button>
          </div>
        </div>
      ) : (
        <>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 text-[11px]">
            <dt className="text-muted-foreground">Project</dt>
            <dd className="font-medium text-foreground/85">{understanding.project}</dd>
            <dt className="text-muted-foreground">Workflow</dt>
            <dd className="font-medium text-foreground/85">{understanding.workflow}</dd>
          </dl>
          {understanding.status === "needs-clarification" && (
            <div className="mt-2 rounded border border-warning/25 bg-warning/5 px-2 py-1.5">
              <p className="text-[10px] font-medium text-warning">Needs clarification</p>
              {understanding.question && (
                <p className="mt-0.5 text-[11px] text-foreground/80">{understanding.question}</p>
              )}
            </div>
          )}
          {understanding.source === "user-corrected" && (
            <p className="mt-2 text-[10px] text-muted-foreground">Corrected by you</p>
          )}
          {error && <p className="mt-1 text-[10px] text-destructive">{error}</p>}
        </>
      )}
    </section>
  );
}

function ContinuitySummary({ continuity }: { continuity: AgentOsContinuity }) {
  const hasSnapshot = continuity.goal || continuity.now || continuity.next;
  if (!hasSnapshot && continuity.follow_up_count === 0) return null;
  const sourceUpdatedAt = continuity.source_updated_at
    ? new Date(continuity.source_updated_at)
    : null;
  const sourceUpdatedLabel =
    sourceUpdatedAt && !Number.isNaN(sourceUpdatedAt.getTime())
      ? sourceUpdatedAt.toLocaleTimeString(undefined, {
          hour: "numeric",
          minute: "2-digit",
        })
      : null;

  return (
    <section
      data-testid="agent-os-continuity"
      className="rounded-md border border-border bg-background/55 px-2.5 py-2"
    >
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.1em] text-muted-foreground">
          <GoalIcon className="size-3.5" />
          Continuity
        </p>
        <span className="text-[10px] text-muted-foreground">Live from Agent OS</span>
      </div>
      <dl className="mt-2 space-y-2 text-[11px] leading-relaxed">
        {continuity.goal && (
          <div>
            <dt className="font-medium text-muted-foreground">Goal</dt>
            <dd className="text-foreground/85">{continuity.goal}</dd>
          </div>
        )}
        {continuity.now && (
          <div>
            <dt className="font-medium text-muted-foreground">Now</dt>
            <dd className="text-foreground/85">{continuity.now}</dd>
          </div>
        )}
        {continuity.next && (
          <div>
            <dt className="font-medium text-muted-foreground">Next</dt>
            <dd className="text-foreground/85">{continuity.next}</dd>
          </div>
        )}
      </dl>
      {sourceUpdatedLabel && (
        <time
          dateTime={continuity.source_updated_at ?? undefined}
          className="mt-2 block text-[10px] text-muted-foreground"
        >
          Source updated {sourceUpdatedLabel}
        </time>
      )}
      {continuity.follow_up_count > 0 && (
        <details className="mt-2 border-t border-border pt-2 text-[11px]">
          <summary className="cursor-pointer font-medium text-muted-foreground">
            {continuity.follow_up_count} remembered{" "}
            {continuity.follow_up_count === 1 ? "follow-up" : "follow-ups"}
          </summary>
          <ul className="mt-2 space-y-2">
            {continuity.follow_ups.slice(0, 3).map((followUp) => (
              <li key={followUp.id}>
                <p className="font-medium text-foreground/85">{followUp.title}</p>
                <p className="text-muted-foreground">{followUp.next_action}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function WorkerEvidence({
  icon: Icon,
  text,
  attention = false,
}: {
  icon: AgentRowIcon;
  text: string;
  attention?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-1.5 rounded-md border border-border bg-background/55 px-2 py-1.5 text-muted-foreground",
        attention && "border-warning/25 bg-warning/5 text-warning",
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      <span className="truncate">{text}</span>
    </div>
  );
}

function ViewModeToggle({
  viewMode,
  onViewModeChange,
}: {
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
}) {
  return (
    <div className="flex shrink-0 items-center justify-end gap-0.5 border-b px-2 py-1">
      <Button
        variant={viewMode === "list" ? "secondary" : "ghost"}
        size="icon-xs"
        onClick={() => onViewModeChange("list")}
        aria-label="List view"
        title="List view"
        data-testid="view-mode-list"
      >
        <ListIcon className="size-3.5" />
      </Button>
      <Button
        variant={viewMode === "graph" ? "secondary" : "ghost"}
        size="icon-xs"
        onClick={() => onViewModeChange("graph")}
        aria-label="Graph view"
        title="Graph view"
        data-testid="view-mode-graph"
      >
        <NetworkIcon className="size-3.5" />
      </Button>
    </div>
  );
}

// Quiet states show only an indicator — the word lives in the tooltip — so the
// row stays clean. Working is quiet too: the pulsing pink dot already reads as
// "active", so the redundant "Working" label is dropped. The eye still lands on
// agents that need input or are in trouble, which keep their word.
const QUIET_STATE: Record<AgentActivity, boolean> = {
  launching: false,
  working: true,
  awaiting: false,
  failed: false,
  // Quiet — show only the grey dot (the word lives in the tooltip), like the
  // idle/done/working dot states. The colored dot is enough to flag the
  // liveness loss without adding label text to the row.
  disconnected: true,
  other: false,
  done: true,
  idle: true,
};

// Settled states are de-emphasized (dimmed) so live agents dominate the list.
// Kept separate from QUIET_STATE: ``working`` is quiet (no label word) but must
// NOT be dimmed — an actively-working agent should stay full-strength.
const SETTLED_STATE: Record<AgentActivity, boolean> = {
  launching: false,
  working: false,
  awaiting: false,
  failed: false,
  // Not dimmed — a disconnected runner is something the user may want to
  // notice and act on (retry/reconnect), so it stays full-strength.
  disconnected: false,
  other: false,
  done: true,
  idle: true,
};

/**
 * Map a sub-agent type label to a category icon so a mix of agents reads by
 * role at a glance (Claude Code spawns many same-type "Explore" agents — the
 * icon distinguishes roles; the preview line below distinguishes instances).
 * Category icons are monochrome — the row applies the muted color; the
 * fallback is the full-color Otto (starfish) mascot.
 *
 * @param tool - The agent type, e.g. ``"Explore"`` or ``"researcher"``;
 *   ``null`` when the child carries no type.
 * @returns An SVG icon component.
 */
export function iconForAgentType(tool: string | null): AgentRowIcon {
  const t = (tool ?? "").toLowerCase();
  if (t.includes("explore")) return SearchIcon;
  if (t.includes("research")) return BookOpenIcon;
  if (t.includes("plan") || t.includes("architect")) return CompassIcon;
  if (t.includes("review")) return ScanSearchIcon;
  if (t.includes("test")) return FlaskConicalIcon;
  if (t.includes("doc") || t.includes("writ")) return FileTextIcon;
  if (
    t.includes("code") ||
    t.includes("eng") ||
    t.includes("dev") ||
    t.includes("front") ||
    t.includes("back")
  ) {
    return Code2Icon;
  }
  return OttoIcon;
}

/**
 * Pick a brand glyph for coding child sessions when the summary carries
 * enough identity metadata. Native children identify via their wrapper
 * label (authoritative — a custom scaffold agent merely *named* "codex"
 * must not get the Codex logo). Pi children are scaffold sessions with
 * no wrapper label, so the exact agent name ``"pi"`` is the signal.
 *
 * Only full native sessions get the brand glyph. *Sub-agent* wrapper
 * children (``…-subagent``) deliberately fall through to the role icons
 * (and the Otto fallback) — a native session's sub-agents are all the
 * same brand, so repeating the logo down the tree says nothing, while
 * role icons distinguish what each one is doing.
 *
 * @param child - One child-session summary from the poll or stream.
 * @returns The Claude/Codex/pi glyph component, or ``null`` for generic agents.
 */
function brandChildIcon(child: ChildSessionInfo): AgentRowIcon | null {
  const wrapper = child.labels?.[WRAPPER_LABEL_KEY];
  const nativeAgent = nativeCodingAgentForWrapper(wrapper);
  if (nativeAgent?.iconKind === "claude") return ClaudeIcon;
  if (nativeAgent?.iconKind === "codex") return CodexIcon;
  if (nativeAgent?.iconKind === "opencode") return OpenCodeIcon;
  if (nativeAgent?.iconKind === "pi") return PiIcon;
  if (nativeAgent?.iconKind === "cursor") return CursorIcon;
  if (nativeAgent?.iconKind === "kiro") return KiroIcon;
  if (nativeAgent?.iconKind === "antigravity") return AntigravityIcon;
  if (nativeAgent?.iconKind === "goose") return GooseIcon;
  if (nativeAgent?.iconKind === "kimi") return KimiIcon;
  if (nativeAgent?.iconKind === "hermes") return HermesIcon;
  // Exact match — substring checks would false-match names like "pipeline".
  if (child.tool === PI_AGENT_NAME) return PiIcon;
  return null;
}

/**
 * Indicator + optional label shared by the main and child rows. The working
 * state reuses the sidebar's RunningDot in the same grey tone, so
 * "active" reads identically across the app; other states are a single
 * tokenized dot.
 *
 * The indicator is rendered last (label first) so that, with the indicator
 * right-aligned in the row, every row's dot lands in the same column
 * regardless of label width or whether the label is shown — otherwise a
 * wide label like "Failed" pushes its dot left of a bare "Idle" dot.
 *
 * @param status - The resolved activity + label to render.
 */
function StatusIndicator({ activity, label, details }: AgentStatus) {
  const title = details ? `${label}: ${details}` : label;
  // Awaiting renders the exact same "Needs response" tag as the sidebar
  // (SessionStateBadge) so the approval affordance reads identically across
  // the app. The tag carries its own copy, so the row's separate label word
  // is omitted to avoid duplicating the text.
  if (activity === "awaiting") {
    return (
      <span
        aria-label={title}
        title={title}
        data-testid="subagent-status-dot"
        className="inline-flex shrink-0 items-center text-xs"
      >
        <Badge className="border-transparent bg-warning/15 text-warning">Needs response</Badge>
      </span>
    );
  }
  if (activity === "failed") {
    return (
      <span
        aria-label={title}
        title={title}
        data-testid="subagent-status-dot"
        className="inline-flex shrink-0 items-center gap-1 text-destructive text-xs"
      >
        <span>{label}</span>
        <span
          className={cn(
            "inline-block size-2 shrink-0 rounded-full",
            activityDotClassName("failed"),
          )}
        />
      </span>
    );
  }
  // ``disconnected`` falls through to the quiet default below: it's a
  // QUIET_STATE, so only the grey --muted-foreground dot renders (no inline
  // word) — the cause stays in the tooltip / aria-label. Distinct from the
  // red "Failed" pill above, without repurposing the shared amber --warning.
  //
  // Launching's inline word reads in the blue --session-active hue to match
  // its dot; every other state here keeps the neutral muted text — the verbatim
  // "other" word stays grey, and idle/done/disconnected show no word at all.
  const wrapperTextClass =
    activity === "launching" ? "text-session-active" : "text-muted-foreground";
  return (
    <span
      aria-label={title}
      title={title}
      data-testid="subagent-status-dot"
      className={cn("inline-flex shrink-0 items-center gap-1 text-xs", wrapperTextClass)}
    >
      {!QUIET_STATE[activity] && <span>{label}</span>}
      {activity === "working" ? (
        <RunningDot />
      ) : (
        <span
          className={cn(
            "inline-block size-2 shrink-0 rounded-full",
            activityDotClassName(activity),
          )}
        />
      )}
    </span>
  );
}

/**
 * Pick the primary label for a child-session row.
 *
 * @param child - One child-session summary from the poll or stream.
 * @returns The label shown beside the child icon.
 */
function childPrimaryLabel(child: ChildSessionInfo): string {
  // User-added rows use the reserved "ui:<agent>:<name>" title sentinel;
  // LLM-spawned titles cannot start with "ui:" because the spec validator
  // rejects "ui" as a sub-agent name.
  const isUserAdded = child.title?.startsWith("ui:") ?? false;
  const childWrapper = child.labels?.[WRAPPER_LABEL_KEY];
  const isNativeSubagent =
    childWrapper === CODEX_NATIVE_SUBAGENT_WRAPPER ||
    childWrapper === OPENCODE_NATIVE_SUBAGENT_WRAPPER;
  if (isNativeSubagent && !isUserAdded) {
    return child.tool ?? child.title ?? child.id;
  }
  let titleTask: string | null = null;
  if (child.title?.includes(":")) {
    const titleSuffix = child.title.split(":").slice(1).join(":");
    if (titleSuffix) titleTask = titleSuffix;
  }
  return child.session_name ?? titleTask ?? child.title ?? child.tool ?? child.id;
}

/**
 * First row of the Subagents list — a navigation link back to the
 * parent (root) session. Always present, even when the parent has
 * no children, so the rail is a complete navigation surface for the
 * parent-children tree.
 *
 * The leading icon doubles as the agent-kind indicator: a Claude or
 * Codex glyph for the native wrappers, and a generic bot icon for
 * everything else. Sub-agent rows nest below
 * with their own role icons, so the "main vs sub-agent" distinction
 * is carried by position + nesting connector rather than a pill.
 */
// Cap matches the server's child-session preview so the main row reads
// consistently with the child rows (CSS truncates to one line regardless;
// this just keeps the DOM string bounded).
const MAIN_PREVIEW_MAX_CHARS = 150;

/**
 * Derive a one-line preview of the root session's most recent message from
 * its snapshot items, mirroring the server's child-session preview so the
 * "main" row reads like the child rows below it.
 *
 * Scans newest-first for the last ``message`` item and joins its text
 * content blocks (assistant ``output_text`` / user ``input_text``).
 *
 * @param items - The root session's snapshot items (oldest-first), or
 *   ``undefined`` while the snapshot is still loading.
 * @returns The latest message text, trimmed and length-capped, or ``null``
 *   when the session has no message item yet.
 */
function mainMessagePreview(items: SessionItem[] | undefined): string | null {
  if (!items) return null;
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i];
    if (item.type !== "message") continue;
    const content = (item as { data?: { content?: unknown } }).data?.content;
    if (!Array.isArray(content)) continue;
    const text = content
      .map((block) =>
        block && typeof block === "object" && "text" in block
          ? String((block as { text: unknown }).text)
          : "",
      )
      .join("")
      .trim();
    if (text) {
      return text.length > MAIN_PREVIEW_MAX_CHARS
        ? `${text.slice(0, MAIN_PREVIEW_MAX_CHARS)}…`
        : text;
    }
  }
  return null;
}

/**
 * Resolve a session's brand icon from its native-wrapper ``iconKind``
 * (authoritative for native-terminal sessions) with a harness-substring
 * fallback for plain SDK sessions that carry no wrapper label — e.g.
 * ``omni --harness kimi``, whose ``harness: "kimi"`` would otherwise fall
 * through to the generic bot. Mirrors ``iconForAgent`` in ``AgentCard.tsx``.
 */
function iconForWrapperOrHarness(
  iconKind: string | undefined,
  harness: string | null | undefined,
  isNessie: boolean,
): AgentRowIcon {
  if (iconKind === "claude" || harness?.includes("claude")) return ClaudeIcon;
  if (iconKind === "codex" || harness?.includes("codex")) return CodexIcon;
  if (iconKind === "opencode" || harness?.includes("opencode")) return OpenCodeIcon;
  if (iconKind === "cursor" || harness?.includes("cursor")) return CursorIcon;
  if (iconKind === "kiro" || harness?.includes("kiro")) return KiroIcon;
  if (iconKind === "goose" || harness?.includes("goose")) return GooseIcon;
  if (iconKind === "kimi" || harness?.includes("kimi")) return KimiIcon;
  if (iconKind === "antigravity" || harness?.includes("antigravity")) return AntigravityIcon;
  // Exact match — a substring check would false-match e.g. "openapi".
  if (iconKind === "pi" || harness === "pi") return PiIcon;
  if (isNessie) return NessieIcon;
  return BotIcon;
}

function MainRow({ rootSessionId, isActive }: { rootSessionId: string; isActive: boolean }) {
  const { session } = useSession(rootSessionId);
  const search = railLinkSearch(useLocation().search);
  // Same wrapper-label probe used by the sidebar (Sidebar.tsx) and
  // TerminalFirstContext to decide a session is claude/codex-native.
  const wrapper = session?.labels?.[WRAPPER_LABEL_KEY];
  const nativeAgent = nativeCodingAgentForWrapper(wrapper);
  const isNessie = session?.agentName === "nessie";
  const Icon = iconForWrapperOrHarness(nativeAgent?.iconKind, session?.harness, isNessie);
  // Native wrappers show the product name (mirroring the sidebar) instead
  // of the spec's YAML name (e.g. "claude-native-ui"); other agents show
  // their agent name, with "main" only while the session loads or when it
  // carries no name.
  const label = nativeAgent?.displayName ?? session?.agentName ?? "main";
  const preview = mainMessagePreview(session?.items);
  return (
    <li>
      <Link
        // Drop session-scoped params (``file``, ``diff``, ``comment``,
        // ``view``) when navigating in the rail — those are tied to
        // one session's file-viewer state and must not bleed into the
        // next. Global params like ``?debug=1`` are preserved by
        // ``railLinkSearch`` so debug mode stays on across navigation.
        to={{ pathname: `/c/${rootSessionId}`, search }}
        data-testid="subagent-main-row"
        data-root-session-id={rootSessionId}
        data-agent-kind={
          nativeAgent != null ? `${nativeAgent.key}-native` : isNessie ? "nessie" : "agent"
        }
        className={cn(
          "flex w-full flex-col gap-0.5 px-2.5 py-2 text-left hover:bg-accent/60",
          isActive && "bg-accent",
        )}
      >
        <div className="flex w-full items-center gap-1">
          <Icon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="shrink-0 truncate text-xs font-medium">{label}</span>
          <span className="flex-1" />
          <StatusIndicator {...sessionStatus(session?.status, session?.lastTaskError)} />
        </div>
        {preview && (
          // Indented to align with the title text above: 14px icon + 4px gap.
          <p
            data-testid="subagent-main-preview"
            className="truncate pl-[18px] text-[11px] text-muted-foreground"
          >
            {preview}
          </p>
        )}
      </Link>
    </li>
  );
}

// Indentation: depth 1 keeps the original 24px gutter (pl-6); each
// further level steps in by another 14px so the connector glyphs read
// as a tree.
const ROW_BASE_PADDING_PX = 24;
const ROW_DEPTH_STEP_PX = 14;
const ROW_TOGGLE_SIZE_PX = 16;

function rowPaddingLeft(depth: number): number {
  return ROW_BASE_PADDING_PX + (depth - 1) * ROW_DEPTH_STEP_PX;
}

function SubagentRow({
  child,
  depth,
  conversationId,
  collapsedRows,
  onToggleCollapsed,
}: {
  child: ChildSessionInfo;
  /** Levels below the root, 1 = direct child of "main". */
  depth: number;
  /** The conversation currently rendered in main, for row highlighting. */
  conversationId: string;
  collapsedRows: Record<string, boolean>;
  onToggleCollapsed: (id: string) => void;
}) {
  const collapsed = collapsedRows[child.id] ?? false;
  const status = childStatus(child);
  const search = railLinkSearch(useLocation().search);
  const Icon = brandChildIcon(child) ?? iconForAgentType(child.tool);
  const primary = childPrimaryLabel(child);
  const isActive = conversationId === child.id;
  // De-emphasize settled rows (done/idle) so working/failed agents dominate
  // — but never the row the user is currently viewing.
  const dim = !isActive && SETTLED_STATE[status.activity];
  // This child's own sub-agents, rendered as the next tree level.
  // Disabled (null id) at the depth cap so the fan-out of fetches is
  // bounded; ``useChildSessions`` skips the query entirely for null.
  const { children: grandchildren } = useChildSessions(depth < MAX_TREE_DEPTH ? child.id : null);
  const hasGrandchildren = grandchildren.length > 0;
  const ToggleIcon = collapsed ? ChevronRightIcon : ChevronDownIcon;
  return (
    <>
      <li className="relative">
        {hasGrandchildren && (
          <button
            type="button"
            data-testid="subagent-collapse-toggle"
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand subagents" : "Collapse subagents"}
            style={{ left: rowPaddingLeft(depth) - ROW_TOGGLE_SIZE_PX }}
            className="absolute top-2 z-10 flex size-4 items-center justify-center rounded-sm text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            onClick={(event) => {
              event.stopPropagation();
              onToggleCollapsed(child.id);
            }}
          >
            <ToggleIcon aria-hidden="true" className="size-3.5" />
          </button>
        )}
        <Link
          // See MainRow: drop session-scoped params on rail navigation
          // (preserving global ones like ``?debug=1``) so a sticky
          // ``?file=`` from the previous session doesn't carry over.
          to={{ pathname: `/c/${child.id}`, search }}
          data-testid="subagent-row"
          data-child-session-id={child.id}
          data-depth={depth}
          // Left gutter (depth-stepped) + connector glyph nests this row
          // under its parent, signaling where it sits in the tree.
          style={{ paddingLeft: rowPaddingLeft(depth) }}
          className={cn(
            "flex w-full flex-col gap-0.5 py-2 pr-2.5 text-left hover:bg-accent/60",
            isActive && "bg-accent",
            dim && "opacity-60 hover:opacity-100",
          )}
        >
          <div className="flex w-full items-center gap-1">
            {hasGrandchildren ? (
              <span aria-hidden="true" className="-ml-3 size-3 shrink-0" />
            ) : (
              <CornerDownRightIcon
                // Decorative nesting connector — the role icon beside it carries
                // the meaning, so hide this from the accessibility tree.
                aria-hidden="true"
                className="-ml-3 size-3 shrink-0 text-muted-foreground/60"
              />
            )}
            <Icon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="shrink-0 truncate text-xs font-medium">{primary}</span>
            <span className="flex-1" />
            <StatusIndicator {...status} />
          </div>
          {child.last_message_preview && (
            // Preview indented to align with the title text on the row
            // above: 12px connector - 12px (-ml-3) + 4px gap + 14px bot
            // icon + 4px gap = 22px. Relative to the row's own padding,
            // so it tracks the depth-stepped gutter automatically.
            <p className="truncate pl-[22px] text-[11px] text-muted-foreground">
              {child.last_message_preview}
            </p>
          )}
        </Link>
      </li>
      {!collapsed &&
        grandchildren.map((grandchild) => (
          <SubagentRow
            key={grandchild.id}
            child={grandchild}
            depth={depth + 1}
            conversationId={conversationId}
            collapsedRows={collapsedRows}
            onToggleCollapsed={onToggleCollapsed}
          />
        ))}
    </>
  );
}
