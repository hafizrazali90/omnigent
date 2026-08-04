import { Columns2Icon, Loader2Icon, PlusIcon, SearchIcon, XIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { PageScroll } from "@/components/PageScroll";
import {
  PageEmptyState,
  PageErrorState,
  PageHeader,
  PageLoadingState,
} from "@/components/PagePresentation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  const [taskSearch, setTaskSearch] = useState("");
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
  const normalizedSearch = taskSearch.trim().toLocaleLowerCase();
  const matchingSessions = sessions.filter((session) => {
    if (!normalizedSearch) return true;
    return [
      session.title,
      session.workspace,
      session.agent_name,
      ...Object.values(session.labels ?? {}),
    ].some((value) => value?.toLocaleLowerCase().includes(normalizedSearch));
  });

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
      <PageScroll contentClassName="px-6">
        <PageHeader title="Split Focus" description="Work with multiple sessions side by side." />
        <PageLoadingState label="Loading sessions…" />
      </PageScroll>
    );
  }

  if (query.isError) {
    return (
      <PageScroll contentClassName="px-6">
        <PageHeader title="Split Focus" description="Work with multiple sessions side by side." />
        <PageErrorState message="Couldn’t load sessions." onRetry={() => void query.refetch()} />
      </PageScroll>
    );
  }

  if (sessions.length === 0) {
    return (
      <PageScroll contentClassName="px-6">
        <PageHeader title="Split Focus" description="Work with multiple sessions side by side." />
        <PageEmptyState
          icon={Columns2Icon}
          title="No sessions yet"
          description="Start two sessions to work with them side by side."
          actions={
            <Button asChild>
              <Link to="/">New session</Link>
            </Button>
          }
        />
      </PageScroll>
    );
  }

  return (
    <section
      data-testid="split-focus-page"
      className="flex min-h-0 flex-1 flex-col overflow-hidden px-6 pb-4"
    >
      <PageHeader
        className="mb-4 shrink-0 py-4"
        title="Split Focus"
        description="Work with multiple sessions side by side."
        actions={
          <>
            <div className="relative">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                type="search"
                aria-label="Search Split Focus tasks"
                value={taskSearch}
                onChange={(event) => setTaskSearch(event.target.value)}
                placeholder="Find a task…"
                className="w-56 pl-8"
              />
            </div>
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
            {paneIds.length < MAX_PANES && availableToAdd && (
              <Button type="button" variant="outline" onClick={addPane}>
                <PlusIcon className="size-4" />
                Add workspace
              </Button>
            )}
          </>
        }
      />

      {normalizedSearch && matchingSessions.length === 0 && (
        <div className="mb-3 rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
          No loaded sessions match. Try another search or load older sessions.
        </div>
      )}

      {sessions.length === 1 && (
        <div className="mb-3 rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
          Start one more session to work side by side.
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto pb-1 [scrollbar-width:thin]">
        {paneIds.map((sessionId, index) => {
          const session = sessions.find((row) => row.id === sessionId);
          if (!session) return null;
          const title = session.title?.trim() || "Untitled session";
          const frameUrl = rebasePath(`/c/${encodeURIComponent(session.id)}?split-pane=1`);
          const pickerSessions = [
            session,
            ...matchingSessions.filter((option) => option.id !== session.id),
          ];

          return (
            <article
              key={session.id}
              data-testid="split-focus-pane"
              className="flex min-h-0 min-w-[28rem] flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card"
            >
              <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
                <Select value={session.id} onValueChange={(value) => replacePane(index, value)}>
                  <SelectTrigger
                    aria-label={`Workspace ${index + 1}`}
                    className="min-w-0 flex-1 font-medium"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {pickerSessions.map((option) => (
                      <SelectItem
                        key={option.id}
                        value={option.id}
                        disabled={option.id !== session.id && paneIds.includes(option.id)}
                      >
                        {option.title?.trim() || "Untitled session"}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
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
