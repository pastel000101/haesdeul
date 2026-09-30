"""ML 고정값 — 운영 모델 이름 · 봉인 개봉 성능표 · 답에 쓰는 라벨 · 예측 시각의 시간대.

2026-09-29 재구성 BL-017 에 여러 파일에 흩어져 있던 값을 모았다. **값과 주석은 그대로다.**

```text
KST                  예측 시각 표기 (+09:00)          ← qa_graph.py · repository.py 두 벌
OPS_MODELS · KIND_OF 적재가 읽는 운영 모델 · 가격 종류  ← repository.py
OPS_MODEL            질의응답이 읽는 운영 번들          ← qa_tools.py
SEALED_ACCURACY …    봉인 개봉 성능표와 그 조건         ← qa_tools.py
BATCH_STATUS_LABEL … 배치 · 보고서 이름과 라벨          ← qa_tools.py
KIND_LABEL           가격 종류를 사람 말로              ← qa_graph.py
```

★ **이 파일은 앱 모듈을 import 하지 않는다** (표준 라이브러리만). 어느 계층이 읽어도
  순환이 생기지 않는다.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

#: 예측 시각의 시간대. 전달표의 예측 시각은 기준일 06:00 KST 로 찍힌다 — UTC 로 보여 주면
#: «왜 전날이냐» 가 된다.
#:
#: ★ **고정 오프셋(+09:00)이다.** 지역 시간대(`app/core/clock.py::SEOUL`)와 뜻이 다르지만
#:   1988 년 뒤로는 값이 같다. 적재가 채우는 기준일 06:00 과 답에 적는 «… KST» 가 이 값을
#:   쓴다. 전에는 `qa_graph.py` 와 `repository.py` 가 한 벌씩 만들었다 — 한 자리로 모았고,
#:   고정 오프셋을 만드는 곳은 `tests/core/test_clock_is_the_only_wall_clock.py` 가 센다.
KST = timezone(timedelta(hours=9))

# ── 운영 모델 ─────────────────────────────────────────────────────────────

#: 운영 모델만 읽는다. 실험 모델(model_*·old-*)이 섞이면 조용히 다른 값이 나간다.
OPS_MODELS = ("ops_auc", "ops_whsl", "ops_rtl")
KIND_OF = {"auc": "AUC", "whsl": "WHSL", "rtl": "RTL"}

#: 운영 번들만 읽는다. 실험 번들(`ops-*` 붙임표)이 섞이면 조용히 다른 값이 나온다.
OPS_MODEL = {"AUC": "ops_auc", "WHSL": "ops_whsl", "RTL": "ops_rtl"}

# ── 봉인 개봉 성능표 ─────────────────────────────────────────────────────

#: 봉인 개봉(2026-09-01) 실측 절대 오차. **화면과 같은 값을 쓰기 위한 표다.**
#:
#: 조건 — 운영 모델 · 홀드아웃 2024~2025 · 486 기준일 · 리드타임 3 이상.
#: 경락가 세 칸은 화면(`app/api/forecast/presenter.py::_ACCURACY`)에 그대로 적혀 있고,
#: 나머지 여섯 칸은 같은 실행의 값이다 (`CLAUDE.md` 절대 오차표).
SEALED_ACCURACY: dict[tuple[str, str], dict[str, Any]] = {
    ("AUC", "배추"):  {"avg": "963원", "err": "190원", "pct": "19.7"},
    ("AUC", "무"):    {"avg": "771원", "err": "144원", "pct": "18.6"},
    ("AUC", "양파"):  {"avg": "1,098원", "err": "98원", "pct": "9.0"},
    ("WHSL", "배추"): {"avg": "1,603원", "err": "296원", "pct": "18.5"},
    ("WHSL", "무"):   {"avg": "1,108원", "err": "158원", "pct": "14.3"},
    ("WHSL", "양파"): {"avg": "1,347원", "err": "99원", "pct": "7.3"},
    ("RTL", "배추"):  {"avg": "4,814원", "err": "614원", "pct": "12.7"},
    ("RTL", "무"):    {"avg": "2,497원", "err": "241원", "pct": "9.7"},
    ("RTL", "양파"):  {"avg": "2,231원", "err": "184원", "pct": "8.3"},
}

#: 이 값이 나온 실행. 답변에 조건 없이 숫자만 적지 않기 위해 같이 내보낸다.
#:
#: ★ **사람 말로 적는다** (마스터 요청 · 2026-09-15). 전에는 «봉인 개봉 · 홀드아웃 ·
#:   리드타임 3 이상» 이었는데 그건 우리끼리 쓰는 말이라 화면에서 읽히지 않는다.
#:   **조건을 뺀 것이 아니라 같은 조건을 아는 말로** 바꿨다 — 언제 · 무엇으로 · 몇 일치인지가
#:   그대로 들어 있다.
SEALED_SOURCE = "2026-09-01 에 2024~2025 실제 가격 486일치로 채점한 값입니다"

# ── 배치 결과 · 점검 보고서 ────────────────────────────────────────────

#: 배치 상태를 사람 말로. **답 문장에 코드 값을 적지 않는다** (마스터 요청).
BATCH_STATUS_LABEL: dict[str, str] = {
    "ok": "정상",
    "fail": "실패",
    "partial": "일부 실패",
    "running": "도는 중",
}

#: 배치 단계 이름을 사람 말로. `run_batch.py` 의 `STAGES` 설명과 같은 말이다.
#:
#: ★ 모르는 단계가 오면 **이름을 그대로 적는다.** 단계가 늘었을 때 «실패한 단계가
#:   있는데 무엇인지 안 알려주는» 답이 되는 것보다 낫다.
STAGE_LABEL: dict[str, str] = {
    "collect_auction": "경락가 수집",
    "collect_price": "중도매·소매가 수집",
    "collect_weather": "기상 수집",
    "collect_volume": "반입량 수집",
    "collect_econ": "경제지표 수집",
    "precheck": "수집 검사",
    "rebuild": "학습표 재생성",
    "predict": "추론",
    "shadow": "그림자 실행",
    "load": "예측 적재",
    "score": "실제값 채점",
    "push": "매입 파트로 넘기기",
}

#: 그날 AI 점검 보고서. 사람이 읽을 글이 `body` 에 통째로 들어 있다.
CHECK_REPORT = "claude_check"

#: 재학습 이야기를 담은 보고서 둘. 앞이 «후보를 찾았나», 뒤가 «견줘 보니 어떤가».
RETRAIN_REPORTS: tuple[str, ...] = ("재학습판정", "재학습검증")

# ── 답에 쓰는 말 ───────────────────────────────────────────────────────

KIND_LABEL = {"AUC": "경락가", "WHSL": "중도매가", "RTL": "소매가"}
