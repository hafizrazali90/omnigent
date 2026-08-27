import { useCallback, useMemo, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon, PlusIcon, RotateCcwIcon, XIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import {
  isVersionConflict,
  summarizeWorkItems,
  WORK_STATUSES,
  WORK_STATUS_LABEL,
} from "@/lib/workTreeApi";
import type { WorkDeliveryState, WorkItem, WorkStatus } from "@/lib/workTreeApi";
import { useWorkTree } from "@/hooks/useWorkTree";
import type { WorkTreeNode } from "@/hooks/useWorkTree";

/**
 * Status glyphs. Deliberately text, not colour alone: the rail has to read
 * the same in light and dark, and to a user who cannot distinguish the hues.
 */
const STATUS_GLYPH: Record<WorkStatus, string> = {
  not_started: "○",
  working: "●",
  waiting: "◷",
  paused: "Ⅱ",
  blocked: "!",
  done: "✓",
};

const STATUS_TONE: Record<WorkStatus, string> = {
  not_started: "text-muted-foreground",
  working: "text-success",
  waiting: "text-muted-foreground",
  paused: "text-muted-foreground",
  blocked: "text-destructive",
  done: "text-success",
};

/**
 * Delivery vocabulary in plain words. Kept visually and semantically apart
 * from status: a green `✓` says the work is finished, never that anything
 * was committed, merged, or deployed.
 */
const DELIVERY_LABEL: Record<WorkDeliveryState, string> = {
  local: "Local only",
  committed: "Committed",
  pushed: "Pushed",
  pr_open: "PR open",
  merged: "Merged",
  deployed: "Deployed",
  live_checked: "Checked live",
  monitored: "Monitored",
  accepted: "Accepted",
  closed: "Closed",
};

const SOURCE_LABEL: Record<string, string> = {
  user: "You",
  orchestrator: "Orchestrator",
  worker: "Worker",
  provider_todo: "Agent checklist",
  discovered: "Discovered",
  resumed: "Resumed",
};

/** The rail shows session → task → subtask and no deeper. */
const MAX_DEPTH = 3;

function StatusGlyph({ status }: { status: WorkStatus }) {
  return (
    <span
      aria-hidden="true"
      data-testid={`work-item-status-${status}`}
      className={cn("w-3 shrink-0 text-center text-[11px] leading-none", STATUS_TONE[status])}
    >
      {STATUS_GLYPH[status]}
    </span>
  );
}

function WorkItemDetails({
  item,
  homeProjectId,
}: {
  item: WorkItem;
  homeProjectId: string | null;
}) {
  const rows: { label: string; value: string }[] = [];
  if (item.why) rows.push({ label: "Why it matters", value: item.why });
  if (item.evidence) rows.push({ label: "Evidence", value: item.evidence });
  if (item.deliveryState)
    rows.push({ label: "Delivery", value: DELIVERY_LABEL[item.deliveryState] });
  if (item.status === "blocked") rows.push({ label: "Blocked", value: "Needs you to unblock it" });
  rows.push({ label: "Source", value: SOURCE_LABEL[item.sourceKind] ?? item.sourceKind });
  if (item.nextAction) rows.push({ label: "Next", value: item.nextAction });
  if (item.projectId && item.projectId !== homeProjectId)
    rows.push({ label: "Project", value: item.projectId });

  return (
    <dl
      data-testid={`work-item-details-${item.id}`}
      className="mt-1 space-y-0.5 border-l border-border pl-2 text-[11px] leading-relaxed"
    >
      {rows.map((row) => (
        <div key={row.label} className="flex gap-1.5">
          <dt className="shrink-0 text-muted-foreground">{row.label}</dt>
          <dd className="min-w-0 break-words text-foreground/80">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function WorkItemRow({
  node,
  depth,
  homeProjectId,
  onStatusChange,
  onDelete,
  onDefer,
  onResume,
  onAddChild,
  onRename,
}: {
  node: WorkTreeNode;
  depth: number;
  homeProjectId: string | null;
  onStatusChange: (item: WorkItem, status: WorkStatus) => void;
  onDelete: (item: WorkItem) => void;
  onDefer: (item: WorkItem) => void;
  onResume: (item: WorkItem) => void;
  onAddChild: (item: WorkItem) => void;
  onRename: (item: WorkItem, title: string) => void;
}) {
  const { item, children } = node;
  // Completed branches auto-collapse so finished work stops competing for
  // attention, but stay in the tree and can always be reopened.
  const [expanded, setExpanded] = useState(!item.collapsed && item.status !== "done");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.title);

  const hasDetail = Boolean(
    item.why ||
    item.evidence ||
    item.deliveryState ||
    item.nextAction ||
    children.length > 0 ||
    item.status === "blocked",
  );

  const commitRename = () => {
    setEditing(false);
    const next = draft.trim();
    if (next && next !== item.title) onRename(item, next);
    else setDraft(item.title);
  };

  return (
    <li data-testid={`work-item-${item.id}`} data-depth={depth}>
      <div className="group flex items-start gap-1.5 rounded px-1.5 py-1 hover:bg-muted/50">
        {hasDetail ? (
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={expanded ? `Collapse ${item.title}` : `Expand ${item.title}`}
            onClick={() => setExpanded((open) => !open)}
            className="mt-px shrink-0 text-muted-foreground hover:text-foreground"
          >
            {expanded ? (
              <ChevronDownIcon className="size-3" />
            ) : (
              <ChevronRightIcon className="size-3" />
            )}
          </button>
        ) : (
          <span className="w-3 shrink-0" />
        )}

        <StatusGlyph status={item.status} />

        <div className="min-w-0 flex-1">
          {editing ? (
            <Input
              autoFocus
              value={draft}
              aria-label={`Rename ${item.title}`}
              componentId="workTree.rename"
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitRename();
                if (e.key === "Escape") {
                  setDraft(item.title);
                  setEditing(false);
                }
              }}
              className="h-6 text-xs"
            />
          ) : (
            <button
              type="button"
              onClick={() => setEditing(true)}
              aria-label={`Edit ${item.title}`}
              className={cn(
                "block w-full break-words text-left text-xs leading-snug",
                item.status === "done" && "text-muted-foreground",
              )}
            >
              {item.title}
            </button>
          )}

          {item.brief && !editing && (
            <p className="break-words text-[11px] leading-snug text-muted-foreground">
              {item.brief}
            </p>
          )}

          {item.projectId && item.projectId !== homeProjectId && (
            <span
              data-testid={`work-item-project-${item.id}`}
              className="mt-0.5 inline-block rounded bg-muted px-1 text-[10px] text-muted-foreground"
            >
              {item.projectId}
            </span>
          )}

          {item.deliveryState && (
            <span
              data-testid={`work-item-delivery-${item.id}`}
              className="ml-1 mt-0.5 inline-block rounded border border-border px-1 text-[10px] text-muted-foreground"
            >
              {DELIVERY_LABEL[item.deliveryState]}
            </span>
          )}

          {expanded && hasDetail && <WorkItemDetails item={item} homeProjectId={homeProjectId} />}
        </div>

        <select
          aria-label={`Status of ${item.title}`}
          value={item.status}
          onChange={(e) => onStatusChange(item, e.target.value as WorkStatus)}
          className="h-5 max-w-24 shrink-0 rounded border border-border bg-card px-1 text-[10px]"
        >
          {WORK_STATUSES.map((status) => (
            <option key={status} value={status}>
              {WORK_STATUS_LABEL[status]}
            </option>
          ))}
        </select>

        <div className="flex shrink-0 items-center gap-0.5 opacity-0 focus-within:opacity-100 group-hover:opacity-100">
          {item.deferredAt === null ? (
            <button
              type="button"
              aria-label={`Defer ${item.title}`}
              onClick={() => onDefer(item)}
              className="text-muted-foreground hover:text-foreground"
            >
              <span aria-hidden="true" className="text-[11px] leading-none">
                Ⅱ
              </span>
            </button>
          ) : (
            <button
              type="button"
              aria-label={`Resume ${item.title}`}
              onClick={() => onResume(item)}
              className="text-muted-foreground hover:text-foreground"
            >
              <RotateCcwIcon className="size-3" />
            </button>
          )}
          {depth < MAX_DEPTH && (
            <button
              type="button"
              aria-label={`Add subtask under ${item.title}`}
              onClick={() => onAddChild(item)}
              className="text-muted-foreground hover:text-foreground"
            >
              <PlusIcon className="size-3" />
            </button>
          )}
          <button
            type="button"
            aria-label={`Delete ${item.title}`}
            onClick={() => onDelete(item)}
            className="text-muted-foreground hover:text-destructive"
          >
            <XIcon className="size-3" />
          </button>
        </div>
      </div>

      {expanded && children.length > 0 && (
        <ul className="ml-4 border-l border-border pl-1">
          {children.map((child) => (
            <WorkItemRow
              key={child.item.id}
              node={child}
              depth={depth + 1}
              homeProjectId={homeProjectId}
              onStatusChange={onStatusChange}
              onDelete={onDelete}
              onDefer={onDefer}
              onResume={onResume}
              onAddChild={onAddChild}
              onRename={onRename}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

/**
 * The session's durable Work Tree, rendered in the right rail's task area.
 *
 * Replaces the transient provider checklist that used to live here. Provider
 * todo events still update this tree, but they no longer own it: what a user
 * wrote, deferred, or marked as delivered survives a provider that forgets it.
 *
 * @param sessionId - Session whose tree to show.
 * @param homeProjectId - The session's own project; an item is only marked
 *   with a project when it differs from this one.
 * @param frameless - Drop the panel's own borders when the host supplies them.
 */
export function WorkTree({
  sessionId,
  homeProjectId = null,
  frameless = false,
}: {
  sessionId: string | null | undefined;
  homeProjectId?: string | null;
  frameless?: boolean;
}) {
  const { nodes, items, isLoading, isError, refetch, create, update, remove, defer, resume } =
    useWorkTree(sessionId);
  const [draftTitle, setDraftTitle] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);
  const [addingUnder, setAddingUnder] = useState<WorkItem | null>(null);

  const handleError = useCallback((error: unknown) => {
    setConflict(
      isVersionConflict(error)
        ? "This item changed somewhere else. Reloaded the latest."
        : "That change didn’t stick. Try again.",
    );
  }, []);

  const onStatusChange = useCallback(
    (item: WorkItem, status: WorkStatus) => {
      setConflict(null);
      update.mutate(
        { itemId: item.id, input: { version: item.version, status } },
        { onError: handleError },
      );
    },
    [handleError, update],
  );

  const onRename = useCallback(
    (item: WorkItem, title: string) => {
      setConflict(null);
      update.mutate(
        { itemId: item.id, input: { version: item.version, title } },
        { onError: handleError },
      );
    },
    [handleError, update],
  );

  const onDelete = useCallback(
    (item: WorkItem) => {
      setConflict(null);
      remove.mutate(
        { itemId: item.id, version: item.version },
        {
          onError: (error) => {
            // A parent refuses to take its children down with it — say why
            // rather than showing the generic conflict copy.
            setConflict(
              isVersionConflict(error)
                ? "Move or delete its subtasks first."
                : "Couldn’t delete that item.",
            );
          },
        },
      );
    },
    [remove],
  );

  const onDefer = useCallback(
    (item: WorkItem) => {
      setConflict(null);
      defer.mutate({ itemId: item.id, version: item.version }, { onError: handleError });
    },
    [defer, handleError],
  );

  const onResume = useCallback(
    (item: WorkItem) => {
      setConflict(null);
      resume.mutate({ itemId: item.id, version: item.version }, { onError: handleError });
    },
    [handleError, resume],
  );

  const submitDraft = useCallback(() => {
    const title = draftTitle.trim();
    if (!title) return;
    setConflict(null);
    create.mutate(
      { title, parentId: addingUnder?.id ?? null },
      {
        onError: handleError,
        onSuccess: () => {
          setDraftTitle("");
          setAddingUnder(null);
        },
      },
    );
  }, [addingUnder, create, draftTitle, handleError]);

  const progress = useMemo(() => summarizeWorkItems(items), [items]);

  if (!sessionId) return null;

  return (
    <div
      data-testid="work-tree"
      className={cn(
        "flex flex-1 flex-col bg-card",
        !frameless && "border-t border-b border-border",
      )}
    >
      <div className="space-y-1.5 px-2 pt-2 pb-1.5" data-testid="implementation-progress">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
            Implementation tree
          </p>
          <span className="text-[10px] tabular-nums text-muted-foreground">
            {progress.completed}/{progress.total} · {WORK_STATUS_LABEL[progress.status]}
          </span>
        </div>
        <Progress
          value={progress.percent}
          aria-label={`Implementation progress ${progress.completed} of ${progress.total}, ${WORK_STATUS_LABEL[progress.status]}`}
        />
      </div>

      {conflict && (
        <div
          role="status"
          data-testid="work-tree-conflict"
          className="mx-2 mb-1 flex items-center gap-2 rounded border border-border bg-muted/40 px-2 py-1 text-[11px]"
        >
          <span className="flex-1">{conflict}</span>
          <Button
            variant="ghost"
            size="sm"
            className="h-5 px-1.5 text-[10px]"
            onClick={() => {
              setConflict(null);
              void refetch();
            }}
          >
            Reload
          </Button>
        </div>
      )}

      {isLoading && (
        <p data-testid="work-tree-loading" className="px-2 py-3 text-[11px] text-muted-foreground">
          Loading the work tree…
        </p>
      )}

      {isError && !isLoading && (
        <div
          role="alert"
          data-testid="work-tree-error"
          className="mx-2 my-2 flex items-center gap-2 rounded border border-destructive/30 bg-destructive/5 px-2 py-1 text-[11px]"
        >
          <span className="flex-1">Couldn’t load the work tree.</span>
          <Button
            variant="outline"
            size="sm"
            className="h-5 px-1.5 text-[10px]"
            onClick={() => void refetch()}
          >
            Retry
          </Button>
        </div>
      )}

      {!isLoading && !isError && items.length === 0 && (
        <p data-testid="work-tree-empty" className="px-2 py-3 text-[11px] text-muted-foreground">
          No implementation steps yet. Add the first step or ask the agent to create a plan.
        </p>
      )}

      {items.length > 0 && (
        <ul data-testid="work-tree-items" className="min-h-0 flex-1 overflow-y-auto px-1 pb-1">
          {nodes.map((node) => (
            <WorkItemRow
              key={node.item.id}
              node={node}
              depth={1}
              homeProjectId={homeProjectId}
              onStatusChange={onStatusChange}
              onDelete={onDelete}
              onDefer={onDefer}
              onResume={onResume}
              onAddChild={setAddingUnder}
              onRename={onRename}
            />
          ))}
        </ul>
      )}

      <div className="flex items-center gap-1 border-t border-border px-2 py-1.5">
        <Input
          value={draftTitle}
          componentId="workTree.add"
          data-testid="work-tree-add-input"
          placeholder={addingUnder ? `Subtask of “${addingUnder.title}”` : "Add work…"}
          aria-label={addingUnder ? `Add subtask under ${addingUnder.title}` : "Add work item"}
          onChange={(e) => setDraftTitle(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitDraft();
            if (e.key === "Escape") setAddingUnder(null);
          }}
          className="h-6 text-xs"
        />
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-1.5"
          aria-label="Add work item"
          data-testid="work-tree-add-button"
          onClick={submitDraft}
        >
          <PlusIcon className="size-3" />
        </Button>
      </div>
    </div>
  );
}
