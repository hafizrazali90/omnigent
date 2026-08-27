import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ArrowUpRightIcon,
  Columns2Icon,
  EllipsisIcon,
  LayoutGridIcon,
  Loader2Icon,
  SendIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { PageScroll } from "@/components/PageScroll";
import {
  PageEmptyState,
  PageErrorState,
  PageHeader,
  PageLoadingState,
} from "@/components/PagePresentation";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useConversations, type Conversation } from "@/hooks/useConversations";
import { useControlRoomLane } from "@/hooks/useControlRoomLane";
import { useSession } from "@/hooks/useSession";
import { useWorkTree } from "@/hooks/useWorkTree";
import { CLAUDE_NATIVE_MODELS } from "@/lib/claudeNativeModels";
import { findNativeModelOption } from "@/lib/codexNativeModels";
import { parseEvent } from "@/lib/sse";
import type { Session } from "@/lib/types";
import type { WorkItem } from "@/lib/workTreeApi";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";

function workspaceName(workspace: string | null | undefined): string {
  if (!workspace) return "No workspace";
  return workspace.split("/").filter(Boolean).at(-1) ?? workspace;
}

function taskState(session: Conversation): {
  label: string;
  tone: string;
  dot: string;
} {
  if ((session.pending_elicitations_count ?? 0) > 0) {
    return {
      label: "Needs you",
      tone: "border-warning/25 bg-warning/10 text-warning",
      dot: "bg-warning",
    };
  }
  if (session.status === "failed") {
    return {
      label: "Needs review",
      tone: "border-destructive/25 bg-destructive/10 text-destructive",
      dot: "bg-destructive",
    };
  }
  if (session.status === "running") {
    return {
      label: "Working",
      tone: "border-primary/20 bg-primary/10 text-primary",
      dot: "bg-primary",
    };
  }
  return {
    label: "Idle",
    tone: "border-border bg-muted/60 text-muted-foreground",
    dot: "bg-muted-foreground",
  };
}

type ControlRoomDensity = "comfortable" | "compact";

interface CurrentTaskSummary {
  label: string;
  text: string;
}

function titleCase(value: string): string {
  return value
    .split(/[-_]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function formatControlRoomModel(session: Session | null): string | null {
  if (!session) return null;
  const usageModels = Object.keys(session.usageByModel ?? {});
  const model =
    session.modelOverride ?? session.llmModel ?? (usageModels.length === 1 ? usageModels[0] : null);
  const raw = model?.trim();
  if (!raw) return null;

  const advertised = findNativeModelOption(session.codexModelOptions ?? [], raw);
  if (advertised) return advertised.displayName ?? advertised.id;

  const leaf =
    raw
      .split("/")
      .at(-1)
      ?.replace(/^system\.ai\./, "") ?? raw;
  const alias = CLAUDE_NATIVE_MODELS.find((option) => option.id === leaf.toLowerCase());
  if (alias) return `Claude ${alias.label}`;

  const claude = leaf.match(/^claude-(opus|sonnet|haiku|fable)-(.+)$/i);
  if (claude) {
    return `Claude ${titleCase(claude[1])} ${claude[2].replace(/-(?=\d)/g, ".")}`;
  }

  const gpt = leaf.replace(/^databricks-/, "").match(/^gpt-(\d+)[.-](\d+)(?:-(.+))?$/i);
  if (gpt) return `GPT-${gpt[1]}.${gpt[2]}${gpt[3] ? ` ${titleCase(gpt[3])}` : ""}`;

  const gptFamily = leaf.replace(/^databricks-/, "").match(/^gpt-([a-z0-9.]+)(?:-(.+))?$/i);
  if (gptFamily) {
    return `GPT-${gptFamily[1]}${gptFamily[2] ? ` ${titleCase(gptFamily[2])}` : ""}`;
  }

  return titleCase(leaf);
}

function pendingQuestion(session: Session | null): string | null {
  for (const raw of session?.pendingElicitations ?? []) {
    const event = parseEvent("response.elicitation_request", raw);
    if (event?.type === "elicitation_request" && event.message.trim()) return event.message.trim();
  }
  return null;
}

export function currentTaskSummary(
  conversation: Conversation,
  session: Session | null,
  items: WorkItem[],
): CurrentTaskSummary | null {
  const question = pendingQuestion(session);
  if (question) return { label: "Waiting for you", text: question };

  const item =
    items.find((candidate) => candidate.status === "working") ??
    items.find((candidate) => candidate.status === "waiting") ??
    items.find((candidate) => candidate.status === "blocked") ??
    items.find((candidate) => candidate.status === "not_started") ??
    items.find((candidate) => candidate.status === "paused");
  if (!item) return null;

  if (conversation.status === "failed") {
    return { label: "Needs review", text: item.nextAction?.trim() || item.title };
  }
  if (item.status === "working") {
    return {
      label: conversation.status === "running" ? "Working on" : "Pending",
      text: item.title,
    };
  }
  if (item.status === "waiting") {
    return { label: "Waiting", text: item.nextAction?.trim() || item.title };
  }
  if (item.status === "blocked") {
    return { label: "Blocked", text: item.nextAction?.trim() || item.title };
  }
  if (item.status === "paused") return { label: "Paused", text: item.title };
  return { label: "Next", text: item.nextAction?.trim() || item.title };
}

function TaskLane({
  session,
  density,
  canMoveLeft,
  canMoveRight,
  onMoveLeft,
  onMoveRight,
}: {
  session: Conversation;
  density: ControlRoomDensity;
  canMoveLeft: boolean;
  canMoveRight: boolean;
  onMoveLeft: () => void;
  onMoveRight: () => void;
}) {
  const state = taskState(session);
  const title = session.title?.trim() || "Untitled session";
  const lane = useControlRoomLane(session.id);
  const snapshot = useSession(session.id).session;
  const workTree = useWorkTree(session.id);
  const model = formatControlRoomModel(snapshot);
  const taskSummary = currentTaskSummary(session, snapshot, workTree.items);
  const [draft, setDraft] = useState("");
  const canReply = session.permission_level == null || session.permission_level >= 2;

  function submitReply() {
    const message = draft.trim();
    if (!message || lane.isSending || !canReply) return;
    void lane
      .send(message)
      .then(() => {
        setDraft((current) => (current.trim() === message ? "" : current));
      })
      .catch(() => undefined);
  }

  return (
    <article
      data-testid="control-room-lane"
      className={cn(
        "flex min-w-0 flex-col overflow-hidden rounded-xl border border-border bg-card",
        density === "compact" ? "h-[30rem]" : "h-[38rem]",
      )}
    >
      <div
        data-testid="control-room-lane-header"
        className={cn(
          "border-b border-border",
          density === "compact" ? "px-3 py-2.5" : "px-4 py-4",
        )}
      >
        <div className="flex min-w-0 items-center justify-between gap-2">
          <p className="truncate text-xs font-medium text-muted-foreground">
            {workspaceName(session.workspace)}
          </p>
          <div className="flex shrink-0 items-center gap-0.5">
            <Button asChild variant="ghost" size="icon-xs">
              <Link
                to={`/c/${encodeURIComponent(session.id)}`}
                aria-label={`Open task: ${title}`}
                title="Open task"
              >
                <ArrowUpRightIcon className="size-3.5" />
              </Link>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button type="button" variant="ghost" size="icon-xs" aria-label="More task actions">
                  <EllipsisIcon className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem disabled={!canMoveLeft} onSelect={onMoveLeft}>
                  <ArrowLeftIcon className="size-4" />
                  Move task left
                </DropdownMenuItem>
                <DropdownMenuItem disabled={!canMoveRight} onSelect={onMoveRight}>
                  <ArrowRightIcon className="size-4" />
                  Move task right
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link to={`/split-focus?session=${encodeURIComponent(session.id)}`}>
                    <Columns2Icon className="size-4" />
                    Open in Split Focus
                  </Link>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
        <h2 className="mt-1 line-clamp-2 text-sm font-semibold leading-snug text-foreground">
          {title}
        </h2>
        <div className="mt-1.5 flex min-w-0 items-center justify-between gap-2">
          {model ? (
            <p className="truncate text-xs font-medium text-muted-foreground" title={model}>
              {model}
            </p>
          ) : (
            <span />
          )}
          <Badge variant="outline" className={cn("shrink-0 gap-1.5", state.tone)}>
            <span className={cn("size-1.5 rounded-full", state.dot)} />
            {state.label}
          </Badge>
        </div>
      </div>

      <div
        className={cn(
          "flex min-h-0 flex-1 flex-col",
          density === "compact" ? "gap-2.5 px-3 py-2.5" : "gap-4 px-4 py-4",
        )}
      >
        {taskSummary && (
          <div
            data-testid="control-room-task-state"
            className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 border-l-2 border-primary/35 px-2.5 py-1 text-xs"
          >
            <span className="font-semibold text-muted-foreground">{taskSummary.label}</span>
            <span className="line-clamp-2 font-medium text-foreground/85" title={taskSummary.text}>
              {taskSummary.text}
            </span>
          </div>
        )}

        <div
          className={cn(
            "flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-background/65",
          )}
        >
          <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
            Conversation
          </div>
          <div
            data-testid="control-room-lane-transcript"
            className="flex flex-1 flex-col gap-2 overflow-y-auto px-3 py-3"
          >
            {lane.isLoadingMessages ? (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2Icon className="size-3 animate-spin" />
                Loading conversation…
              </p>
            ) : lane.messagesError ? (
              <p className="text-xs text-destructive">Couldn’t load this conversation.</p>
            ) : lane.messages.length === 0 ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                No recent messages yet. You can continue this task below.
              </p>
            ) : (
              lane.messages.map((message) => (
                <div
                  key={message.id}
                  data-role={message.role}
                  className={cn(
                    "max-w-[92%] rounded-lg px-2.5 py-2 leading-relaxed",
                    density === "compact" ? "text-xs" : "text-sm",
                    message.role === "user"
                      ? "ml-auto bg-primary text-primary-foreground"
                      : "bg-muted text-foreground",
                  )}
                >
                  <p>{message.text}</p>
                  {message.pending && (
                    <p className="mt-1 text-[0.6875rem] opacity-70">Sent · syncing</p>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        <form
          className="mt-auto flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            submitReply();
          }}
        >
          <Textarea
            aria-label={`Reply to ${title}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={canReply ? "Reply to this task…" : "You have read-only access"}
            disabled={!canReply || lane.isSending}
            rows={1}
            className="max-h-20 min-h-10 resize-none py-2"
          />
          <Button
            type="submit"
            size="icon"
            className="size-10 md:size-10"
            aria-label="Send reply"
            disabled={!canReply || lane.isSending || !draft.trim()}
          >
            {lane.isSending ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SendIcon className="size-4" />
            )}
          </Button>
        </form>
        {lane.sendError && (
          <p role="alert" className="text-xs text-destructive">
            Couldn’t send this reply. Your draft is still here.
          </p>
        )}
      </div>
    </article>
  );
}

export function ControlRoomPage() {
  const query = useConversations("", false, { reconcileWhileConnected: true });
  const [search, setSearch] = useState("");
  const [columns, setColumns] = useState<2 | 3 | 4>(() => {
    if (typeof window === "undefined") return 4;
    const stored = Number(window.localStorage.getItem("agent-os.control-room.columns"));
    return stored === 2 || stored === 3 || stored === 4 ? stored : 4;
  });
  const [density, setDensity] = useState<ControlRoomDensity>(() => {
    if (typeof window === "undefined") return "compact";
    return window.localStorage.getItem("agent-os.control-room.density.v2") === "comfortable"
      ? "comfortable"
      : "compact";
  });
  const [manualOrder, setManualOrder] = useState<string[]>(() => {
    if (typeof window === "undefined") return [];
    try {
      const stored = JSON.parse(window.localStorage.getItem("agent-os.control-room.order") || "[]");
      return Array.isArray(stored) && stored.every((id) => typeof id === "string") ? stored : [];
    } catch {
      return [];
    }
  });
  const sessions = (query.data?.pages ?? [])
    .flatMap((page) => page.data)
    .filter((session) => !session.parent_session_id);
  const orderedSessions = useMemo(() => {
    const rank = new Map(manualOrder.map((id, index) => [id, index]));
    return [...sessions].sort((left, right) => {
      const leftRank = rank.get(left.id);
      const rightRank = rank.get(right.id);
      if (leftRank == null && rightRank == null) return 0;
      if (leftRank == null) return 1;
      if (rightRank == null) return -1;
      return leftRank - rightRank;
    });
  }, [manualOrder, sessions]);
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const visibleSessions = orderedSessions.filter((session) => {
    if (!normalizedSearch) return true;
    return [
      session.title,
      session.workspace,
      session.agent_name,
      ...Object.values(session.labels ?? {}),
    ].some((value) => value?.toLocaleLowerCase().includes(normalizedSearch));
  });
  const needsYou = sessions.filter(
    (session) => (session.pending_elicitations_count ?? 0) > 0 || session.status === "failed",
  ).length;
  const working = sessions.filter((session) => session.status === "running").length;
  useEffect(() => {
    window.localStorage.setItem("agent-os.control-room.columns", String(columns));
  }, [columns]);

  useEffect(() => {
    window.localStorage.setItem("agent-os.control-room.density.v2", density);
  }, [density]);

  useEffect(() => {
    window.localStorage.setItem("agent-os.control-room.order", JSON.stringify(manualOrder));
  }, [manualOrder]);

  function moveSession(sessionId: string, direction: -1 | 1) {
    const ids = orderedSessions.map((session) => session.id);
    const index = ids.indexOf(sessionId);
    const nextIndex = index + direction;
    if (index < 0 || nextIndex < 0 || nextIndex >= ids.length) return;
    [ids[index], ids[nextIndex]] = [ids[nextIndex], ids[index]];
    setManualOrder(ids);
  }

  return (
    <PageScroll
      data-testid="control-room-page"
      maxWidthClassName={
        !query.isLoading && !query.isError && sessions.length > 0 ? "max-w-none" : "max-w-3xl"
      }
      contentClassName="px-6"
    >
      <PageHeader
        title="Control Room"
        description="View and continue your active sessions in one place."
        actions={
          !query.isLoading && !query.isError && sessions.length > 0 ? (
            <>
              <Badge variant="outline">{sessions.length} sessions</Badge>
              <Badge variant="secondary">{working} working</Badge>
              {needsYou > 0 && (
                <Badge variant="outline" className="border-warning/25 text-warning">
                  {needsYou} need you
                </Badge>
              )}
            </>
          ) : undefined
        }
      />

      {!query.isLoading && !query.isError && sessions.length > 0 && (
        <div className="mb-4 flex flex-wrap items-end gap-2">
          <Input
            type="search"
            aria-label="Search tasks"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search tasks…"
            className="min-w-52 flex-1 md:max-w-sm"
          />
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Columns
            <Select
              value={String(columns)}
              onValueChange={(value) => setColumns(Number(value) as 2 | 3 | 4)}
            >
              <SelectTrigger aria-label="Visible columns" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="2">2</SelectItem>
                <SelectItem value="3">3</SelectItem>
                <SelectItem value="4">4</SelectItem>
              </SelectContent>
            </Select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Density
            <Select
              value={density}
              onValueChange={(value) => setDensity(value as ControlRoomDensity)}
            >
              <SelectTrigger aria-label="Density" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="comfortable">Comfortable</SelectItem>
                <SelectItem value="compact">Compact</SelectItem>
              </SelectContent>
            </Select>
          </label>
          {query.hasNextPage && (
            <Button
              type="button"
              variant="outline"
              disabled={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              {query.isFetchingNextPage && <Loader2Icon className="size-4 animate-spin" />}
              Load more tasks
            </Button>
          )}
        </div>
      )}

      {query.isError ? (
        <PageErrorState message="Couldn’t load sessions." onRetry={() => void query.refetch()} />
      ) : query.isLoading ? (
        <PageLoadingState label="Loading sessions…" />
      ) : sessions.length === 0 ? (
        <PageEmptyState
          icon={LayoutGridIcon}
          title="No sessions yet"
          description="Start a session and it will appear here."
          actions={
            <Button asChild>
              <Link to="/">New session</Link>
            </Button>
          }
        />
      ) : visibleSessions.length === 0 ? (
        <PageEmptyState
          icon={LayoutGridIcon}
          title="No sessions found"
          description="Try another search or load older sessions."
        />
      ) : (
        <div
          data-testid="control-room-lanes"
          data-columns={columns}
          data-density={density}
          className={cn("grid overflow-x-auto pb-4", density === "compact" ? "gap-3" : "gap-4")}
          style={{
            gridTemplateColumns: `repeat(${columns}, minmax(${density === "compact" ? "17rem" : "22rem"}, 1fr))`,
          }}
        >
          {visibleSessions.map((session) => {
            const orderedIndex = orderedSessions.findIndex((row) => row.id === session.id);
            return (
              <TaskLane
                key={session.id}
                session={session}
                density={density}
                canMoveLeft={orderedIndex > 0}
                canMoveRight={orderedIndex < orderedSessions.length - 1}
                onMoveLeft={() => moveSession(session.id, -1)}
                onMoveRight={() => moveSession(session.id, 1)}
              />
            );
          })}
        </div>
      )}
    </PageScroll>
  );
}
