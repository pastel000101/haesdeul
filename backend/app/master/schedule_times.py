"""
schedule_times.py — **스케줄러 하루의 시각 상수.** 벽시계는 읽지 않는다.

★ 값의 **뜻**(언제 깨우고 언제 마감하나)은 스케줄러(`scheduler.py`)의 것이고, 여기는 그
  숫자를 두는 자리다. `scheduler.py` 가 이름을 다시 내보내고(`__all__`), `sim_time.py` 가
  `SCHEDULE_START` 를 기준점으로 가져간다.

★ **왜 따로 떼었나** (2026-09-29). 이 상수들은 2026-09-08 부터 `master/clock.py` 에 있었다
  (아래 이유). 그 파일의 벽시계 · 시간대가 `app/core/clock.py` 로 옮겨 가면서, 09:30 에
  깨어나 10:30 에 마감한다는 **스케줄러 업무 값**은 도메인을 모르는 `core` 로 보내지 않고
  마스터에 남겼다. 이 파일도 표준 라이브러리만 들이는 **leaf** 라 누가 가져가도 고리가
  안 생긴다.
"""

from __future__ import annotations

from datetime import time, timedelta

__all__ = [
    "SCHEDULE_DEADLINE",
    "SCHEDULE_INTERVAL",
    "SCHEDULE_START",
]


# ── 하루의 시각 상수 ────────────────────────────────────────────────────
#
# ★ **이름을 붙여 둔다.** 09:30 · 5분 · 10:30 이 코드 여기저기에 숫자로 흩어지면
#   마감을 11:00 로 옮기는 날 한 군데를 빠뜨리고, 그 한 군데가 조용히 다르게 돈다.
#
# 🔴 **왜 `scheduler.py` 가 아니라 여기 있나** (2026-09-08 · 옮겨 왔다).
#
#   `sim_time.phase_instant` 가 기준점으로 `SCHEDULE_START` 를 쓴다. 그것을
#   `scheduler` 에서 가져오면 `sim_time → scheduler → service` 를 끌어오고,
#   `service` 가 나중에 `phase_instant` 를 쓰는 날 **순환이 난다.**
#
#   ★ 그 경로는 가설이 아니다 — `sim_time` 이 먹여 살리려는 자리가 정확히
#     `scheduler` 가 모는 자리다.
#
#   (2026-09-08 에 옮긴 곳은 `master/clock.py` 였다. 2026-09-29 그 파일이 `core` 로 가면서
#   이 상수들만 이 파일로 옮겼다 — 이유는 그대로다.)

#: ML 적재(09:23 쯤)가 끝났을 시각. **첫 깨어남.**
SCHEDULE_START: time = time(9, 30)

#: `WAIT` 일 때 다음 깨어남까지. **판단을 안 돌리고 그냥 다시 온다.**
SCHEDULE_INTERVAL: timedelta = timedelta(minutes=5)

#: 마감. 여기를 넘기면 **한 번 돌려 `E4_NOT_STARTED` 로 확정 기록**한다.
#:
#: ⚠️ 마감을 안 두면 예측이 영영 안 오는 날 스케줄러가 하루 종일 `WAIT` 하고,
#:   *"오늘 못 돌았다"* 가 어디에도 안 남는다.
SCHEDULE_DEADLINE: time = time(10, 30)
