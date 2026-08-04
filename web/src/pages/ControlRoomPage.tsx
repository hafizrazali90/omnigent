import {
  ArrowUpRightIcon,
  BotIcon,
  CircleAlertIcon,
  Loader2Icon,
  MessageSquareMoreIcon,
  RadioIcon,
  SendIcon,
  TriangleAlertIcon,
} from "lucide-react";
import { useState } from "react";
import { PageScroll } from "@/components/PageScroll";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useConversations, type Conversation } from "@/hooks/useConversations";
import { useControlRoomLane } from "@/hooks/useControlRoomLane";
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
    label: "Ready",
    tone: "border-border bg-muted/60 text-muted-foreground",
    dot: "bg-muted-foreground",
  };
}

function TaskLane({ session }: { session: Conversation }) {
  const state = taskState(session);
  const title = session.title?.trim() || "Untitled session";
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
      className="flex min-h-[36rem] w-[min(24rem,calc(100vw-3rem))] shrink-0 flex-col rounded-[var(--radius-otto-lg)] border border-border bg-card shadow-sm"
    >
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-4">
        <div className="min-w-0">
          <p className="mb-1 truncate text-xs font-medium text-muted-foreground">
            {workspaceName(session.workspace)}
          </p>
          <h2 className="line-clamp-2 text-base font-semibold leading-snug text-foreground">
            {title}
          </h2>
        </div>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-medium",
            state.tone,
          )}
        >
          <span className={cn("size-1.5 rounded-full", state.dot)} />
          {state.label}
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-4 px-4 py-4">
        <div className="rounded-[var(--radius-otto-md)] bg-muted/45 p-3">
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

        <div className="flex min-h-44 flex-1 flex-col overflow-hidden rounded-[var(--radius-otto-md)] border border-border bg-background/65">
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
                    "max-w-[88%] rounded-[var(--radius-otto-md)] px-3 py-2 text-sm leading-relaxed",
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

        <dl className="grid gap-3 text-sm">
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

        <div>
          <Button asChild className="w-full justify-between" variant="outline">
            <Link to={`/c/${encodeURIComponent(session.id)}`}>
              Open task
              <ArrowUpRightIcon className="size-4" />
            </Link>
          </Button>
        </div>
      </div>
    </article>
  );
}

export function ControlRoomPage() {
  const query = useConversations("", false, { reconcileWhileConnected: true });
  const sessions = (query.data?.pages ?? [])
    .flatMap((page) => page.data)
    .filter((session) => !session.parent_session_id);
  const needsYou = sessions.filter(
    (session) => (session.pending_elicitations_count ?? 0) > 0 || session.status === "failed",
  ).length;
  const working = sessions.filter((session) => session.status === "running").length;

  return (
    <PageScroll
      data-testid="control-room-page"
      maxWidthClassName="max-w-none"
      contentClassName="px-5 md:px-8"
    >
      <header className="mb-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Agent OS workspace
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">Control Room</h1>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-muted-foreground">
            Watch your active work together, then enter any task without losing the others.
          </p>
        </div>
        {!query.isLoading && !query.isError && sessions.length > 0 && (
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded-full border border-border bg-card px-3 py-1.5">
              {sessions.length} sessions
            </span>
            <span className="rounded-full border border-primary/20 bg-primary/10 px-3 py-1.5 text-primary">
              {working} working
            </span>
            {needsYou > 0 && (
              <span className="rounded-full border border-warning/25 bg-warning/10 px-3 py-1.5 text-warning">
                {needsYou} need you
              </span>
            )}
          </div>
        )}
      </header>

      {query.isError ? (
        <div
          role="alert"
          className="flex max-w-xl items-center gap-3 rounded-[var(--radius-otto-md)] border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <TriangleAlertIcon className="size-5 shrink-0 text-destructive" />
          <div className="flex-1">
            <p className="font-medium">Couldn’t load your sessions</p>
            <p className="text-muted-foreground">
              Your work is safe. Try loading the overview again.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
            Try again
          </Button>
        </div>
      ) : query.isLoading ? (
        <div className="flex items-center gap-2 py-16 text-sm text-muted-foreground">
          <Loader2Icon className="size-4 animate-spin" />
          Loading your active work…
        </div>
      ) : sessions.length === 0 ? (
        <div className="max-w-xl rounded-[var(--radius-otto-lg)] border border-dashed border-border p-8">
          <h2 className="text-base font-semibold">No active tasks yet</h2>
          <p className="mt-1 mb-5 text-sm leading-relaxed text-muted-foreground">
            Start a normal Omnigent session and it will appear here automatically.
          </p>
          <Button asChild>
            <Link to="/">Start a session</Link>
          </Button>
        </div>
      ) : (
        <div
          data-testid="control-room-lanes"
          className="flex gap-4 overflow-x-auto pb-4 [scrollbar-width:thin]"
        >
          {sessions.map((session) => (
            <TaskLane key={session.id} session={session} />
          ))}
        </div>
      )}
    </PageScroll>
  );
}
