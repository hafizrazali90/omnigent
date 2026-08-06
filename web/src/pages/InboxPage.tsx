/**
 * Needs You page (``/needs-you``; legacy alias ``/inbox``) — approvals,
 * stopped tasks, unseen completed work, and comments across all sessions.
 *
 * Built entirely from existing primitives:
 *
 * - The session list (`useConversations`) already carries
 *   `pending_elicitations_count` per row, kept live by the
 *   `WS /v1/sessions/updates` stream. The inbox drains all list
 *   pages while mounted, since an awaiting session may sit far
 *   below the sidebar's first page.
 * - Each session's snapshot (`GET /v1/sessions/{id}`) already replays
 *   the full pending `response.elicitation_request` event dicts; the
 *   per-session query key includes the row's count so a count change
 *   pushed over the socket refetches exactly that session.
 * - Cards are the same `ApprovalCard` the chat renders, with a local
 *   submit handler (the chat store is single-conversation, so the
 *   inbox posts the verdict itself via `approve()` — same endpoint).
 *
 * Only the first (newest) card is expanded by default; the rest
 * collapse to a one-line summary row so a long backlog stays
 * scannable. Clicking a row toggles it; manual toggles stick even
 * as new items arrive (overrides are keyed by elicitation id).
 *
 * Below the approvals, the inbox lists unseen file comments — draft
 * comments other users left on session files (`useCommentInbox`),
 * each iconed with the author's avatar pill. A comment clears when
 * it's actually opened in the file browser — the FileViewer records
 * it in the client-side seen registry (`useSeenComments`) while the
 * comments panel is open on its file; the "Open file" link deep-links
 * to exactly that (`?file=` + `?comment=` auto-opens the panel).
 *
 * Deliberately NOT here for approvals: read/unread state, dismiss,
 * mentions — none of those exist as server concepts. Resolving (or
 * the prompt timing out) is what clears an approval.
 */

import { useEffect, useRef, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRightIcon,
  CircleCheckIcon,
  ChevronDownIcon,
  InboxIcon,
  Loader2Icon,
  OctagonAlertIcon,
} from "lucide-react";
import { ApprovalCard, type SubmitApprovalFn } from "@/components/blocks/ApprovalCard";
import { PageScroll } from "@/components/PageScroll";
import {
  PageEmptyState,
  PageErrorState,
  PageHeader,
  PageLoadingState,
} from "@/components/PagePresentation";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useCommentInbox } from "@/hooks/useCommentInbox";
import { useConversations } from "@/hooks/useConversations";
import {
  collectInboxItems,
  collectSessionAttention,
  type InboxItem,
  type InboxSource,
} from "@/lib/inbox";
import {
  clearUnreadOverride,
  isConversationUnseen,
  markConversationSeen,
  useUnseenTick,
} from "@/hooks/useUnseenConversations";
import { relativeTime } from "@/lib/relativeTime";
import { Link } from "@/lib/routing";
import { approve, getSession } from "@/lib/sessionsApi";
import { userColor, userInitials } from "@/lib/userBadge";
import { cn } from "@/lib/utils";
import { conversationDisplayLabel, getConversationAgentType } from "@/shell/sidebarNav";

/** Optimistic verdicts keyed by elicitation id, mirroring the chat store's flip. */
type RespondedMap = Record<
  string,
  { action: "accept" | "decline"; content?: Record<string, unknown> }
>;

export function InboxPage() {
  const queryClient = useQueryClient();
  const conversationsQuery = useConversations("", false, { reconcileWhileConnected: true });
  const [responded, setResponded] = useState<RespondedMap>({});
  // Manual expand/collapse toggles keyed by elicitation id. Anything
  // not in the map falls back to the default: expanded only for the
  // first (newest) item. Keying by id (not index) keeps a user's
  // explicit toggles stable when new items shift positions.
  const [expandedOverrides, setExpandedOverrides] = useState<Record<string, boolean>>({});

  // The sidebar pages lazily on scroll, but the inbox must consider
  // EVERY session — an approval can be pending in a session that 20+
  // newer sessions have since pushed off the first page. Drain the
  // remaining pages while the inbox is mounted (the query cache is
  // shared with the sidebar, so this also completes its badge).
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = conversationsQuery;
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const allRows = (conversationsQuery.data?.pages ?? []).flatMap((page) => page.data);
  const rows = allRows.filter((c) => !c.archived && (c.pending_elicitations_count ?? 0) > 0);
  // Subscribe to read-state changes so "completed since you last looked"
  // clears as soon as that task is opened.
  useUnseenTick();
  const sessionAttention = collectSessionAttention(allRows, (row) =>
    isConversationUnseen(row.id, row.updated_at, row.status),
  );

  // Unseen file comments across sessions — the hook filters to rows
  // that report comments and mounts one comments query per such row.
  const commentInbox = useCommentInbox(allRows);

  // One snapshot fetch per session that reports pending prompts. The
  // count rides in the query key, so the WS count patch (new prompt,
  // resolved-elsewhere prompt) naturally triggers a refetch; a row
  // dropping to zero falls out of `rows` and its query is dropped.
  // `retry: 1` absorbs a transient blip without hammering a down
  // server; persistent failures surface in the error banner below.
  const snapshotQueries = useQueries({
    queries: rows.map((row) => ({
      queryKey: ["inbox-elicitations", row.id, row.pending_elicitations_count, row.updated_at],
      queryFn: () => getSession(row.id),
      retry: 1,
    })),
  });

  const sources: InboxSource[] = [];
  rows.forEach((row, i) => {
    const snapshot = snapshotQueries[i]?.data;
    if (snapshot) {
      sources.push({
        row,
        pendingElicitations: snapshot.pendingElicitations ?? [],
        canApprove: snapshot.canApprove ?? true,
      });
    }
  });
  const items = collectInboxItems(sources);

  // Clear stale optimistic verdicts when snapshot data refreshes.
  // If a hook retry re-parks the same elicitation id after the user
  // approved the previous attempt, the local `responded` entry would
  // otherwise keep the card stuck on "Approved" indefinitely. When
  // any snapshot query delivers fresh data (dataUpdatedAt advances),
  // sweep verdicts whose id is still pending on the server — those
  // approvals were consumed and the server re-parked the prompt.
  const snapshotVersionKey = snapshotQueries.map((q) => q.dataUpdatedAt ?? 0).join(",");
  const isFirstRender = useRef(true);
  useEffect(() => {
    // Skip the first render — there are no stale verdicts yet.
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    setResponded((prev) => {
      if (Object.keys(prev).length === 0) return prev;
      const pendingIds = new Set(items.map((i) => i.elicitation.elicitationId));
      const stale = Object.keys(prev).filter((id) => pendingIds.has(id));
      if (stale.length === 0) return prev;
      return Object.fromEntries(Object.entries(prev).filter(([id]) => !pendingIds.has(id)));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshotVersionKey]);

  // "Settled" gating for the empty state: while the session list is
  // still paging or ANY snapshot is in flight, an empty `items` only
  // means "not assembled yet" — showing "No approvals waiting" then
  // would be a lie. Failed snapshots also block the empty state (their
  // approvals exist, we just couldn't fetch them) and get a banner.
  const assembling =
    conversationsQuery.isLoading ||
    hasNextPage ||
    isFetchingNextPage ||
    snapshotQueries.some((q) => q.isLoading) ||
    commentInbox.isLoading;
  const failedSnapshots = snapshotQueries.filter((q) => q.isError);
  const failedSessionCount = failedSnapshots.length + commentInbox.failedCount;

  // Mirrors `chatStore.submitApproval`: optimistic flip → resolve POST →
  // rollback on error. Success invalidates the session list so the row's
  // count (and the sidebar badge) drop without waiting for the socket.
  const makeSubmit = (item: InboxItem): SubmitApprovalFn => {
    return (elicitationId, action, content) => {
      setResponded((prev) => ({
        ...prev,
        [elicitationId]: content === undefined ? { action } : { action, content },
      }));
      void approve(
        item.resolveSessionId,
        elicitationId,
        content === undefined ? { action } : { action, content },
      ).then(
        () => {
          void queryClient.invalidateQueries({ queryKey: ["conversations"] });
        },
        () => {
          // Roll back to pending so the buttons reappear and the user
          // can retry — same recovery the chat store uses.
          setResponded((prev) => {
            const { [elicitationId]: _respondedVerdict, ...pendingVerdicts } = prev;
            return pendingVerdicts;
          });
        },
      );
    };
  };

  return (
    <PageScroll contentClassName="px-6">
      <PageHeader
        title="Needs You"
        description="Review approvals, stopped sessions, completed work, and comments."
        actions={
          items.length > 0 || sessionAttention.length > 0 || commentInbox.items.length > 0 ? (
            <Badge variant="outline">
              {[
                items.length > 0 &&
                  (items.length === 1 ? "1 approval" : `${items.length} approvals`),
                sessionAttention.length > 0 &&
                  (sessionAttention.length === 1 ? "1 task" : `${sessionAttention.length} tasks`),
                commentInbox.items.length > 0 &&
                  (commentInbox.items.length === 1
                    ? "1 comment"
                    : `${commentInbox.items.length} comments`),
              ]
                .filter(Boolean)
                .join(" · ")}
            </Badge>
          ) : undefined
        }
      />

      {failedSessionCount > 0 && (
        <PageErrorState
          testId="inbox-load-error"
          className="mb-4"
          message={`Couldn’t load items from ${failedSessionCount} ${
            failedSessionCount === 1 ? "session" : "sessions"
          }.`}
          onRetry={() => {
            failedSnapshots.forEach((q) => void q.refetch());
            commentInbox.retryFailed();
          }}
        />
      )}

      {assembling &&
        items.length === 0 &&
        sessionAttention.length === 0 &&
        commentInbox.items.length === 0 && <PageLoadingState label="Checking what needs you…" />}

      {!assembling &&
        failedSessionCount === 0 &&
        items.length === 0 &&
        sessionAttention.length === 0 &&
        commentInbox.items.length === 0 && (
          <PageEmptyState
            icon={InboxIcon}
            title="Nothing needs you right now"
            description="Approvals, stopped sessions, completed work, and comments will appear here."
          />
        )}

      <div className="flex flex-col gap-4">
        {items.map((item, index) => {
          const elicitationId = item.elicitation.elicitationId;
          const verdict = responded[elicitationId];
          const expanded = expandedOverrides[elicitationId] ?? index === 0;
          // Same display mapping the sidebar uses: native-wrapper
          // sessions read "Claude Code" / "Codex", never the internal
          // agent name ("claude-native-ui"). The agent chip is hidden
          // when it would just repeat the title (untitled native
          // sessions, where the wrapper label IS the display label).
          const title = conversationDisplayLabel(item.row);
          const agentLabel = getConversationAgentType(item.row);
          return (
            <div
              key={elicitationId}
              data-testid="inbox-item"
              data-expanded={expanded}
              className="flex flex-col gap-2 rounded-xl border border-border bg-card p-4"
            >
              <div className="flex items-center gap-2">
                {/* The toggle is a sibling of the Open-session link (not a
                    parent) — nesting a link inside a button is invalid HTML
                    and breaks middle-click/new-tab behavior. */}
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() =>
                    setExpandedOverrides((prev) => ({ ...prev, [elicitationId]: !expanded }))
                  }
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <ChevronDownIcon
                    className={cn(
                      "size-4 shrink-0 text-muted-foreground transition-transform",
                      !expanded && "-rotate-90",
                    )}
                  />
                  <span className="min-w-0 shrink-0 truncate text-ui font-medium">
                    {title}
                    {agentLabel !== title && (
                      <span className="ml-2 text-sm font-normal text-muted-foreground">
                        {agentLabel}
                      </span>
                    )}
                  </span>
                  {!expanded && (
                    <span className="min-w-0 truncate text-sm text-muted-foreground">
                      {item.elicitation.message}
                    </span>
                  )}
                </button>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="text-sm text-muted-foreground">
                    {/* Server timestamps are epoch seconds; relativeTime takes ms. */}
                    {relativeTime(item.row.updated_at * 1000)}
                  </span>
                  <Button asChild variant="ghost" size="sm" className="text-sm">
                    <Link to={`/c/${item.row.id}`}>
                      Open session
                      <ArrowRightIcon className="ml-1 size-3.5" />
                    </Link>
                  </Button>
                </span>
              </div>
              {expanded && (
                <ApprovalCard
                  elicitationId={elicitationId}
                  message={item.elicitation.message}
                  phase={item.elicitation.phase}
                  policyName={item.elicitation.policyName}
                  contentPreview={item.elicitation.contentPreview}
                  requestedSchema={item.elicitation.requestedSchema}
                  url={item.elicitation.url}
                  status={verdict ? "responded" : "pending"}
                  response={verdict ?? null}
                  askUserQuestion={item.elicitation.askUserQuestion}
                  exitPlanMode={item.elicitation.exitPlanMode}
                  codexCommand={item.elicitation.codexCommand}
                  allowAllEdits={item.elicitation.allowAllEdits}
                  rememberScope={item.elicitation.rememberScope}
                  canApprove={item.canApprove}
                  onSubmit={makeSubmit(item)}
                />
              )}
            </div>
          );
        })}
        {sessionAttention.map((item) => {
          const isFailed = item.kind === "failed";
          const title = conversationDisplayLabel(item.row);
          const agentLabel = getConversationAgentType(item.row);
          return (
            <div
              key={`${item.kind}:${item.row.id}`}
              data-testid="needs-you-session"
              data-kind={item.kind}
              className={cn(
                "flex items-start gap-3 rounded-xl border bg-card p-4",
                isFailed ? "border-destructive/30" : "border-border",
              )}
            >
              {isFailed ? (
                <OctagonAlertIcon className="mt-0.5 size-5 shrink-0 text-destructive" />
              ) : (
                <CircleCheckIcon className="mt-0.5 size-5 shrink-0 text-success" />
              )}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">
                  {isFailed ? "Run stopped and needs review" : "Completed since you last looked"}
                </p>
                <p className="mt-1 truncate text-sm">
                  {title}
                  {agentLabel !== title && (
                    <span className="ml-2 text-xs text-muted-foreground">{agentLabel}</span>
                  )}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {isFailed
                    ? "Open the task to see what stopped and decide the next step."
                    : "The worker finished new work while you were elsewhere."}
                </p>
              </div>
              <span className="flex shrink-0 items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {relativeTime(item.row.updated_at * 1000)}
                </span>
                <Button asChild variant="ghost" size="sm" className="text-xs">
                  <Link
                    to={`/c/${item.row.id}`}
                    onClick={() => {
                      clearUnreadOverride(item.row.id);
                      markConversationSeen(item.row.id, item.row.updated_at);
                    }}
                  >
                    Open task
                    <ArrowRightIcon className="ml-1 size-3.5" />
                  </Link>
                </Button>
              </span>
            </div>
          );
        })}
        {commentInbox.items.map((item) => {
          const comment = item.comment;
          // Single-user mode stores no author; mirror CommentsPanel's
          // "You" fallback (the only human in that mode is the viewer).
          const author = comment.created_by ?? "You";
          const sessionTitle = conversationDisplayLabel(item.row);
          return (
            <div
              key={comment.id}
              data-testid="inbox-comment"
              className="flex gap-3 rounded-xl border border-border bg-card p-4"
            >
              {/* The item's icon: the author's avatar pill (same
                  deterministic initials + color as presence circles). */}
              <Avatar size="sm" className="mt-0.5">
                <AvatarFallback
                  className="font-medium text-white"
                  style={{ backgroundColor: userColor(author) }}
                >
                  {userInitials(author)}
                </AvatarFallback>
              </Avatar>
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <div className="flex items-center gap-2">
                  <span className="min-w-0 truncate text-ui">
                    <span className="font-medium">{author}</span>
                    <span className="text-muted-foreground"> commented on </span>
                    <span className="font-mono text-sm">{comment.path}</span>
                  </span>
                  <span className="ml-auto flex shrink-0 items-center gap-2">
                    <span className="text-sm text-muted-foreground">
                      {/* created_at is epoch seconds; relativeTime takes ms. */}
                      {relativeTime(comment.created_at * 1000)}
                    </span>
                    <Button asChild variant="ghost" size="sm" className="text-sm">
                      {/* Deep-link into the file browser with this comment
                          selected — opening it there marks it seen, which
                          is what clears this inbox item. */}
                      <Link
                        to={`/c/${item.row.id}?file=${encodeURIComponent(comment.path)}&comment=${encodeURIComponent(comment.id)}`}
                      >
                        Open file
                        <ArrowRightIcon className="ml-1 size-3.5" />
                      </Link>
                    </Button>
                  </span>
                </div>
                {comment.anchor_content && (
                  <p className="truncate font-mono text-sm text-muted-foreground">
                    {comment.anchor_content.trim()}
                  </p>
                )}
                <p className="line-clamp-3 text-ui break-words whitespace-pre-wrap">
                  {comment.body}
                </p>
                <span className="text-sm text-muted-foreground">{sessionTitle}</span>
              </div>
            </div>
          );
        })}
        {assembling &&
          (items.length > 0 || sessionAttention.length > 0 || commentInbox.items.length > 0) && (
            <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
              <Loader2Icon className="size-3.5 animate-spin" />
              Checking remaining sessions…
            </div>
          )}
      </div>
    </PageScroll>
  );
}
