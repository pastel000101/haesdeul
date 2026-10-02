"use client";

/**
 * 화면이 보는 실행 ID 를 읽는 갈고리와, 그 값을 받은 뒤에만 안쪽을 그리는 문.
 *
 * 값은 `lib/run_context.ts` 가 백엔드에서 받아 둔다. 받기 전 · 실패했을 때는 안쪽을 그리지
 * 않는다 — 안쪽의 조회 · 쓰기가 실행 ID 없이, 또는 다른 실행으로 나가지 않게 한다.
 */

import { useSyncExternalStore, type ReactNode } from "react";

import { ErrorBox, Loading } from "@/components/console/Blocks";
import {
  serverShownRun,
  shownRunSnapshot,
  subscribeShownRun,
  type ShownRun,
} from "@/lib/run_context";

export function useShownRun(): ShownRun {
  return useSyncExternalStore(subscribeShownRun, shownRunSnapshot, serverShownRun);
}

/** 받은 실행 ID. 받기 전 · 실패했으면 `null` 이다. */
export function useShownRunId(): string | null {
  const run = useShownRun();
  return run.status === "ready" ? run.simRunId : null;
}

export function ShownRunGate({
  what,
  children,
}: {
  what: string;
  children: (simRunId: string) => ReactNode;
}) {
  const run = useShownRun();
  if (run.status === "error") return <ErrorBox message={run.message} />;
  if (run.status === "loading") return <Loading what={what} />;
  return <>{children(run.simRunId)}</>;
}
