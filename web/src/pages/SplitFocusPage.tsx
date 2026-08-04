import { Columns2Icon, Loader2Icon, PlusIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { useEffect, useMemo } from "react";
import { Button } from "@/components/ui/button";
import { useConversations, type Conversation } from "@/hooks/useConversations";
import { Link, useRebasePath, useSearchParams } from "@/lib/routing";

const MIN_PANES = 2;
const MAX_PANES = 4;

function selectedSessionIds(requested: string[], sessions: Conversation[]): string[] {
  const available = new Set(sessions.map((session) => session.id));
  const selected = requested.filter(
    (id, index) => available.has(id) && requested.indexOf(id) === index,
  );

  for (const session of sessions) {
    if (selected.length >= Math.min(MIN_PANES, sessions.length)) break;
    if (!selected.includes(session.id)) selected.push(session.id);
  }
  return selected.slice(0, MAX_PANES);
}

function sameSelection(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((id, index) => id === right[index]);
}

export function SplitFocusPage() {
  const query = useConversations("", false, { reconcileWhileConnected: true });
  const [searchParams, setSearchParams] = useSearchParams();
  const rebasePath = useRebasePath();
  const sessions = useMemo(
    () =>
      (query.data?.pages ?? [])
        .flatMap((page) => page.data)
        .filter((session) => !session.parent_session_id),
    [query.data?.pages],
  );
  const requested = searchParams.getAll("session");
  const paneIds = selectedSessionIds(requested, sessions);

  function persistSelection(nextIds: string[]) {
    const next = new URLSearchParams();
    for (const id of nextIds) next.append("session", id);
    setSearchParams(next, { replace: true });
  }

  useEffect(() => {
    if (query.isLoading || query.isError || sameSelection(requested, paneIds)) return;
    const next = new URLSearchParams();
    for (const id of paneIds) next.append("session", id);
    setSearchParams(next, { replace: true });
  }, [paneIds, query.isError, query.isLoading, requested, setSearchParams]);

  function replacePane(index: number, sessionId: string) {
    const next = [...paneIds];
    next[index] = sessionId;
    persistSelection(next);
  }

  function addPane() {
    const nextSession = sessions.find((session) => !paneIds.includes(session.id));
    if (!nextSession || paneIds.length >= MAX_PANES) return;
    persistSelection([...paneIds, nextSession.id]);
  }

  function removePane(sessionId: string) {
    if (paneIds.length <= MIN_PANES) return;
    persistSelection(paneIds.filter((id) => id !== sessionId));
  }

  const availableToAdd = sessions.some((session) => !paneIds.includes(session.id));

  if (query.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" />
        Loading Split Focus…
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="m-6 flex max-w-xl items-center gap-3 rounded-[var(--radius-otto-md)] border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <TriangleAlertIcon className="size-5 shrink-0 text-destructive" />
        <div className="flex-1">
          <p className="font-medium">Couldn’t load your tasks</p>
          <p className="text-muted-foreground">Your sessions are safe. Try Split Focus again.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
          Try again
        </Button>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="m-6 max-w-xl rounded-[var(--radius-otto-lg)] border border-dashed border-border p-8">
        <h1 className="text-lg font-semibold">No tasks available for Split Focus</h1>
        <p className="mt-1 mb-5 text-sm leading-relaxed text-muted-foreground">
          Start two normal Omnigent sessions, then return here to keep them open together.
        </p>
        <Button asChild>
          <Link to="/">Start a session</Link>
        </Button>
      </div>
    );
  }

  return (
    <section
      data-testid="split-focus-page"
      className="flex min-h-0 flex-1 flex-col overflow-hidden px-4 pb-4"
    >
      <header className="flex shrink-0 flex-wrap items-end justify-between gap-3 py-4">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            Agent OS workspace
          </p>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Columns2Icon className="size-5" />
            Split Focus
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Keep complete task workspaces open together without mixing their session state.
          </p>
        </div>
        {paneIds.length < MAX_PANES && availableToAdd && (
          <Button type="button" variant="outline" onClick={addPane}>
            <PlusIcon className="size-4" />
            Add workspace
          </Button>
        )}
      </header>

      {sessions.length === 1 && (
        <div className="mb-3 rounded-[var(--radius-otto-md)] border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
          Start one more session to use Split Focus side by side.
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto pb-1 [scrollbar-width:thin]">
        {paneIds.map((sessionId, index) => {
          const session = sessions.find((row) => row.id === sessionId);
          if (!session) return null;
          const title = session.title?.trim() || "Untitled session";
          const frameUrl = rebasePath(`/c/${encodeURIComponent(session.id)}?split-pane=1`);

          return (
            <article
              key={session.id}
              data-testid="split-focus-pane"
              className="flex min-h-0 min-w-[28rem] flex-1 flex-col overflow-hidden rounded-[var(--radius-otto-lg)] border border-border bg-card shadow-sm"
            >
              <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
                <select
                  aria-label={`Workspace ${index + 1}`}
                  value={session.id}
                  onChange={(event) => replacePane(index, event.target.value)}
                  className="min-w-0 flex-1 rounded-md border border-border bg-background px-2 py-1.5 text-sm font-medium"
                >
                  {sessions.map((option) => (
                    <option
                      key={option.id}
                      value={option.id}
                      disabled={option.id !== session.id && paneIds.includes(option.id)}
                    >
                      {option.title?.trim() || "Untitled session"}
                    </option>
                  ))}
                </select>
                {paneIds.length > MIN_PANES && (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`Remove ${title}`}
                    onClick={() => removePane(session.id)}
                  >
                    <XIcon className="size-4" />
                  </Button>
                )}
              </div>
              {/* oxlint-disable-next-line react/iframe-missing-sandbox -- This is a trusted same-origin Omnigent workspace, not third-party content. It needs the normal app origin and scripts to preserve the complete task experience. */}
              <iframe
                title={`Task workspace: ${title}`}
                src={frameUrl}
                className="min-h-[32rem] w-full flex-1 bg-background"
              />
            </article>
          );
        })}
      </div>
    </section>
  );
}
