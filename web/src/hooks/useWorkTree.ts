import { useCallback, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createWorkItem,
  deferWorkItem,
  deleteWorkItem,
  getWorkTree,
  resumeWorkItem,
  updateWorkItem,
} from "@/lib/workTreeApi";
import type {
  CreateWorkItemInput,
  UpdateWorkItemInput,
  WorkItem,
  WorkTree,
} from "@/lib/workTreeApi";

/** React-query key for one session's durable Work Tree. */
export function workTreeQueryKey(sessionId: string | null | undefined) {
  return ["work-tree", sessionId] as const;
}

/** One node plus its children, ready to render. */
export interface WorkTreeNode {
  item: WorkItem;
  children: WorkTreeNode[];
}

/**
 * Build the nested render shape from the server's flat, ordered list.
 *
 * The server already returns parents before children in sibling order, so
 * this only groups — it never re-sorts, which keeps the displayed order the
 * one the user actually dragged things into.
 *
 * @param items - The tree's items, in server order.
 * @returns Root nodes, each carrying its children.
 */
export function buildWorkTreeNodes(items: WorkItem[]): WorkTreeNode[] {
  const nodes = new Map<string, WorkTreeNode>();
  for (const item of items) nodes.set(item.id, { item, children: [] });

  const roots: WorkTreeNode[] = [];
  for (const item of items) {
    const node = nodes.get(item.id);
    if (!node) continue;
    const parent = item.parentId ? nodes.get(item.parentId) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

/**
 * Read and mutate a session's durable Work Tree.
 *
 * The cache is refreshed two ways that must agree: this query (page load,
 * reconnect) and the `session.work_tree` SSE handler, which writes the same
 * full-state payload into the same key. Because every server payload is the
 * whole tree, a missed event self-heals on the next one — no reconciliation.
 *
 * Mutations send the `version` the caller last saw. A 409 propagates to the
 * caller so the UI can say the item moved on rather than silently clobbering
 * another window's edit; the refetch that follows brings back the truth.
 *
 * @param sessionId - Session identifier, or `null` to stay idle.
 * @returns Tree state plus the mutation helpers the rail needs.
 */
export function useWorkTree(sessionId: string | null | undefined) {
  const queryClient = useQueryClient();
  const queryKey = workTreeQueryKey(sessionId);

  const query = useQuery<WorkTree>({
    queryKey,
    queryFn: () => getWorkTree(sessionId as string),
    enabled: Boolean(sessionId),
    staleTime: 30_000,
  });

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey });
  }, [queryClient, queryKey]);

  const create = useMutation({
    mutationFn: (input: CreateWorkItemInput) => createWorkItem(sessionId as string, input),
    onSettled: invalidate,
  });

  const update = useMutation({
    mutationFn: ({ itemId, input }: { itemId: string; input: UpdateWorkItemInput }) =>
      updateWorkItem(sessionId as string, itemId, input),
    onSettled: invalidate,
  });

  const remove = useMutation({
    mutationFn: ({ itemId, version }: { itemId: string; version: number }) =>
      deleteWorkItem(sessionId as string, itemId, version),
    onSettled: invalidate,
  });

  const defer = useMutation({
    mutationFn: ({ itemId, version }: { itemId: string; version: number }) =>
      deferWorkItem(sessionId as string, itemId, version),
    onSettled: invalidate,
  });

  const resume = useMutation({
    mutationFn: ({ itemId, version }: { itemId: string; version: number }) =>
      resumeWorkItem(sessionId as string, itemId, version),
    onSettled: invalidate,
  });

  const items = useMemo(() => query.data?.items ?? [], [query.data]);
  const nodes = useMemo(() => buildWorkTreeNodes(items), [items]);

  return {
    items,
    nodes,
    relatedProjectIds: query.data?.relatedProjectIds ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
    create,
    update,
    remove,
    defer,
    resume,
  };
}
