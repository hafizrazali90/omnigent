import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { isMessageItem } from "@/lib/conversationItems";
import type { MessageItem } from "@/lib/conversationItems";
import { fetchSessionItemsPage, postEvent } from "@/lib/sessionsApi";

export interface ControlRoomLaneMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  pending?: boolean;
}

const laneQueryKey = (sessionId: string) =>
  ["control-room", "session", sessionId, "messages"] as const;

interface PendingLaneMessage extends ControlRoomLaneMessage {
  pending: true;
  baselineItemIds: Set<string>;
}

const EMPTY_LANE_MESSAGES: ControlRoomLaneMessage[] = [];

function messageText(item: MessageItem): string {
  return item.content
    .filter((block) => block.type === "input_text" || block.type === "output_text")
    .map((block) => block.text ?? "")
    .join("")
    .trim();
}

async function fetchLaneMessages(sessionId: string): Promise<ControlRoomLaneMessage[]> {
  const page = await fetchSessionItemsPage(sessionId, { limit: 12 });
  return page.items
    .filter(isMessageItem)
    .filter((item) => !item.is_meta)
    .map((item) => ({
      id: item.id,
      role: item.role,
      text: messageText(item),
    }))
    .filter((item) => item.text.length > 0)
    .slice(-6);
}

/**
 * Own one lightweight transcript and send lifecycle for one Control Room lane.
 *
 * The session id is part of every query and mutation boundary. This keeps the
 * full-workspace singleton chat store out of the multi-session overview.
 */
export function useControlRoomLane(sessionId: string) {
  const queryClient = useQueryClient();
  const pendingSequence = useRef(0);
  const [pendingMessages, setPendingMessages] = useState<PendingLaneMessage[]>([]);
  const messagesQuery = useQuery({
    queryKey: laneQueryKey(sessionId),
    queryFn: () => fetchLaneMessages(sessionId),
    refetchInterval: 2_500,
  });
  const committedMessages = messagesQuery.data ?? EMPTY_LANE_MESSAGES;

  useEffect(() => {
    setPendingMessages((current) => {
      const next = current.filter(
        (pending) =>
          !committedMessages.some(
            (message) =>
              message.role === "user" &&
              message.text === pending.text &&
              !pending.baselineItemIds.has(message.id),
          ),
      );
      return next.length === current.length ? current : next;
    });
  }, [committedMessages]);

  const sendMutation = useMutation({
    mutationKey: ["control-room", "session", sessionId, "send"],
    mutationFn: async (message: string) => {
      await postEvent(sessionId, {
        type: "message",
        data: {
          role: "user",
          content: [{ type: "input_text", text: message }],
        },
      });
    },
    onMutate: (message) => {
      pendingSequence.current += 1;
      const tempId = `pending_${sessionId}_${pendingSequence.current}`;
      setPendingMessages((current) => [
        ...current,
        {
          id: tempId,
          role: "user",
          text: message,
          pending: true,
          baselineItemIds: new Set(committedMessages.map((item) => item.id)),
        },
      ]);
      return { tempId };
    },
    onError: (_error, _message, context) => {
      if (!context) return;
      setPendingMessages((current) => current.filter((item) => item.id !== context.tempId));
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: laneQueryKey(sessionId) }),
        queryClient.invalidateQueries({ queryKey: ["conversations"] }),
      ]);
    },
  });

  return {
    messages: [
      ...committedMessages,
      ...pendingMessages.map((message) => ({
        id: message.id,
        role: message.role,
        text: message.text,
        pending: true,
      })),
    ],
    isLoadingMessages: messagesQuery.isLoading,
    messagesError: messagesQuery.error instanceof Error ? messagesQuery.error : null,
    isSending: sendMutation.isPending,
    sendError: sendMutation.error instanceof Error ? sendMutation.error : null,
    send: (text: string) => {
      const message = text.trim();
      return message ? sendMutation.mutateAsync(message) : Promise.resolve();
    },
  };
}
