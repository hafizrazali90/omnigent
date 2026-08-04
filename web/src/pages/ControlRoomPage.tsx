import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ArrowUpRightIcon,
  BotIcon,
  CircleAlertIcon,
  Columns2Icon,
  LayoutGridIcon,
  Loader2Icon,
  MessageSquareMoreIcon,
  RadioIcon,
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
import { agentOsUnderstandingFromLabels } from "@/lib/agentOsUnderstanding";
import { relativeTime } from "@/lib/relativeTime";
import { Link } from "@/lib/routing";
import { cn } from "@/lib/utils";

function workspaceName(workspace: string | null | undefined): string {
  if (!workspace) return "No workspace";
  return workspace.split("/").filter(Boolean).at(-1) ?? workspace;
}

function activityLabel(updatedAtSeconds: number): string {
  const relative = relativeTime(updatedAtSeconds * 1000);
  return relative === "now" ? "now" : `${relative} ago`;
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
  const understanding = agentOsUnderstandingFromLabels(session.labels);
  const lane = useControlRoomLane(session.id);
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
        "flex min-w-0 flex-col rounded-xl border border-border bg-card",
        density === "compact" ? "min-h-[30rem]" : "min-h-[36rem]",
      )}
    >
      <div
        className={cn(
          "flex items-start justify-between gap-3 border-b border-border",
          density === "compact" ? "px-3 py-3" : "px-4 py-4",
        )}
      >
        <div className="min-w-0">
          <p className="mb-1 truncate text-xs font-medium text-muted-foreground">
            {workspaceName(session.workspace)}
          </p>
          <h2 className="line-clamp-2 text-base font-semibold leading-snug text-foreground">
            {title}
          </h2>
          <div className="mt-2 flex items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`Move ${title} left`}
              disabled={!canMoveLeft}
              onClick={onMoveLeft}
            >
              <ArrowLeftIcon className="size-3.5" />
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={`Move ${title} right`}
              disabled={!canMoveRight}
              onClick={onMoveRight}
            >
              <ArrowRightIcon className="size-3.5" />
            </Button>
          </div>
        </div>
        <Badge variant="outline" className={cn("shrink-0 gap-1.5", state.tone)}>
          <span className={cn("size-1.5 rounded-full", state.dot)} />
          {state.label}
        </Badge>
      </div>

      <div
        className={cn(
          "flex flex-1 flex-col",
          density === "compact" ? "gap-3 px-3 py-3" : "gap-4 px-4 py-4",
        )}
      >
        {understanding && (
          <dl
            className={cn(
              "grid rounded-lg border border-border bg-background/55 text-sm",
              density === "compact" ? "gap-2 p-2.5" : "gap-3 p-3",
            )}
          >
            <div>
              <dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                Finish line
              </dt>
              <dd className="mt-1 font-medium text-foreground/85">
                {understanding.finishLine || "Not set"}
              </dd>
            </div>
            {understanding.provenState && (
              <div className="border-t border-border/70 pt-3">
                <dt className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                  Current proof
                </dt>
                <dd className="mt-1 font-medium text-foreground/85">{understanding.provenState}</dd>
                {understanding.provenStateEvidence && (
                  <dd className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {understanding.provenStateEvidence}
                  </dd>
                )}
              </div>
            )}
          </dl>
        )}

        <div className={cn("rounded-lg bg-muted/45", density === "compact" ? "p-2.5" : "p-3")}>
          <div className="mb-2 flex items-center gap-2 text-xs font-medium text-muted-foreground">
            <MessageSquareMoreIcon className="size-3.5" />
            Current session
          </div>
          <p className="text-sm leading-relaxed text-foreground/85">
            {session.status === "running"
              ? "The worker is currently progressing this task."
              : (session.pending_elicitations_count ?? 0) > 0
                ? "The worker is waiting for your answer or approval."
                : session.status === "failed"
                  ? "The latest run stopped and needs your review."
                  : "The latest turn is complete and ready to continue."}
          </p>
        </div>

        <div
          className={cn(
            "flex flex-1 flex-col overflow-hidden rounded-lg border border-border bg-background/65",
            density === "compact" ? "min-h-32" : "min-h-44",
          )}
        >
          <div className="border-b border-border px-3 py-2 text-xs font-medium text-muted-foreground">
            Recent conversation
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
                    "max-w-[88%] rounded-lg px-3 py-2 text-sm leading-relaxed",
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

        <dl className={cn("grid text-sm", density === "compact" ? "gap-2" : "gap-3")}>
          <div className="flex items-center justify-between gap-3">
            <dt className="flex items-center gap-2 text-muted-foreground">
              <BotIcon className="size-3.5" /> Worker
            </dt>
            <dd className="truncate font-medium">{session.agent_name || "Agent"}</dd>
          </div>
          <div className="flex items-center justify-between gap-3">
            <dt className="flex items-center gap-2 text-muted-foreground">
              <RadioIcon className="size-3.5" /> Activity
            </dt>
            <dd className="font-medium">{activityLabel(session.updated_at)}</dd>
          </div>
          {(session.pending_elicitations_count ?? 0) > 0 && (
            <div className="flex items-center justify-between gap-3">
              <dt className="flex items-center gap-2 text-warning">
                <CircleAlertIcon className="size-3.5" /> Waiting
              </dt>
              <dd className="font-medium text-warning">
                {session.pending_elicitations_count} request
                {session.pending_elicitations_count === 1 ? "" : "s"}
              </dd>
            </div>
          )}
        </dl>

        <form
          className="mt-auto space-y-2 pt-2"
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
            rows={2}
            className="min-h-16 resize-none"
          />
          {lane.sendError && (
            <p role="alert" className="text-xs text-destructive">
              Couldn’t send this reply. Your draft is still here.
            </p>
          )}
          <Button
            type="submit"
            className="w-full"
            disabled={!canReply || lane.isSending || !draft.trim()}
          >
            {lane.isSending ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SendIcon className="size-4" />
            )}
            Send reply
          </Button>
        </form>

        <div className="flex flex-wrap gap-2">
          <Button asChild className="min-w-40 flex-1 justify-between" variant="outline">
            <Link to={`/c/${encodeURIComponent(session.id)}`}>
              Open task
              <ArrowUpRightIcon className="size-4" />
            </Link>
          </Button>
          <Button asChild className="min-w-40 flex-1 justify-between" variant="outline">
            <Link to={`/split-focus?session=${encodeURIComponent(session.id)}`}>
              Open in Split Focus
              <Columns2Icon className="size-4" />
            </Link>
          </Button>
        </div>
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
    if (typeof window === "undefined") return "comfortable";
    return window.localStorage.getItem("agent-os.control-room.density") === "compact"
      ? "compact"
      : "comfortable";
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
  const columnClass = {
    2: "grid-cols-1 xl:grid-cols-2",
    3: "grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3",
    4: "grid-cols-1 xl:grid-cols-2 2xl:grid-cols-4",
  }[columns];

  useEffect(() => {
    window.localStorage.setItem("agent-os.control-room.columns", String(columns));
  }, [columns]);

  useEffect(() => {
    window.localStorage.setItem("agent-os.control-room.density", density);
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
          className={cn("grid gap-4 pb-4", columnClass)}
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
