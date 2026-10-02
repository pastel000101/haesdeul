/**
 * 화면이 보는 실행 ID(`sim_run_id`)를 받아 두는 자리.
 *
 * 값의 주인은 백엔드 설정 한 곳이다(`backend/app/core/settings.py` 의 `SHOWN_SIM_RUN_ID`).
 * 화면은 `GET /api/console/shown-run` 으로 그 값을 한 번 받아 탭 조회 · 채팅 · 판매 진행 패널 ·
 * 하루 시뮬레이션에 똑같이 싣는다. 코드 상수 · 빌드 값 · 브라우저 저장값으로 따로 정하지
 * 않는다 — 그런 자리가 하나라도 있으면 보는 실행과 기록하는 실행이 조용히 갈린다.
 *
 * 값을 받기 전이거나 받지 못했으면 실행 ID 가 없다. 그때 부르는 쪽은 요청을 보내지 않는다.
 * 다른 실행으로 대신 채워 조회하거나 기록하지 않는다.
 *
 * React 바깥의 저장소라 `useSyncExternalStore` 로 읽는다 (`components/console/ShownRun.tsx`).
 */

import { consoleShownRun } from "./console_api";

export type ShownRun =
  | { status: "loading" }
  | { status: "ready"; simRunId: string }
  | { status: "error"; message: string };

const LOADING: ShownRun = { status: "loading" };

let current: ShownRun = LOADING;
let inFlight = false;
const listeners = new Set<() => void>();

function publish(next: ShownRun): void {
  current = next;
  listeners.forEach((listener) => listener());
}

function load(): void {
  if (inFlight || current.status === "ready") return;
  inFlight = true;
  if (current.status === "error") publish(LOADING);
  consoleShownRun
    .get()
    .then((body) => {
      const simRunId = typeof body.sim_run_id === "string" ? body.sim_run_id.trim() : "";
      publish(
        simRunId
          ? { status: "ready", simRunId }
          : { status: "error", message: "백엔드가 화면 실행 ID 를 비워 보냈습니다." },
      );
    })
    .catch((error: unknown) => {
      const reason = error instanceof Error ? error.message : String(error);
      publish({ status: "error", message: `화면 실행 ID 를 받지 못했습니다 — ${reason}` });
    })
    .finally(() => {
      inFlight = false;
    });
}

export function shownRunSnapshot(): ShownRun {
  return current;
}

/** 서버 렌더에는 백엔드 값이 없다. 항상 «받는 중» 으로 그려야 하이드레이션이 안 어긋난다. */
export function serverShownRun(): ShownRun {
  return LOADING;
}

/** 구독할 때 아직 값이 없으면 받으러 간다. 실패했던 값은 다음 구독(화면 이동) 때 다시 받는다. */
export function subscribeShownRun(listener: () => void): () => void {
  listeners.add(listener);
  load();
  return () => listeners.delete(listener);
}
