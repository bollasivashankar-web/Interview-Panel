import type { QueryClient } from "@tanstack/react-query";

// One boundary per subscription: token refreshes retain data; account changes do not.
export function sessionBoundary(client: QueryClient, update: (userId: string | null) => void) {
  let previousUserId: string | null = null;
  return (userId: string | null) => {
    if (previousUserId !== userId) client.clear();
    previousUserId = userId;
    update(userId);
  };
}
