"""매입 탭 — 값을 읽어오는 곳.

소유: **매입 파트 (우리)**.

★ **DB 가 안 붙어도 화면은 떠야 합니다.** 못 읽으면 예시값으로 떨어지고
  ``Source.filled = False`` 가 되어 「예시값」 딱지가 붙습니다. 화면이 통째로
  죽는 것보다, 예시라고 적힌 화면이 뜨는 편이 낫습니다 (ML 이 `forecast` 에서
  잡은 방식과 같습니다).

★ **여기서 에이전트를 돌리지 않습니다.** 저장된 실행(`master_agent_runs`)과
  확정 매입 원장(`purchases`)을 **읽기만** 합니다. 화면을 열 때마다 LLM 이
  돌면 안 됩니다 (CLAUDE.md 규칙 2 — read-only).

★ **DB 를 여기서 읽지 않습니다** (2026-09-29 재구성 BL-014). 저장된 실행 · 확정 매입 · 결정 ·
  품목 이름 조회는 마스터 readmodel ``read_purchase_tab``(SQL 은
  ``master/repository/purchase_tab.py``)이 합니다 — 전에는 이 모듈의 ``_read`` 가 재무 DB 입구
  (``app.finance.db``)의 조회 헬퍼로 직접 돌렸습니다. 스키마를 ``.env`` 가 정하는 이유(매입
  에이전트 경로와 다르다)는 그 repository 머리말이 이어 적습니다.

★ **look-ahead 를 화면에도 적용합니다.** 확정 매입은 ``purchase_date <= as_of``
  만 봅니다. 그날 화면에 다음 주 매입이 보이면 «그날 알 수 있었던 것» 이
  아니게 됩니다 (규칙 1 의 정신).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.api.primitives import Column, Note, Source, Stat, Table
from app.api.purchase.schema import Plan, PurchaseTab, Reason
from app.contracts.core import ITEMS
from app.master.domain import plan_state
from app.master.readmodel.purchase_record import RecordedTotals, recorded_totals_by_plan
from app.master.readmodel.purchase_tab import read_purchase_tab

log = logging.getLogger(__name__)

_LEG_COLS = [
    Column(key="leg", label="회차"),
    Column(key="buy", label="사는 날", mono=True),
    Column(key="qty", label="수량", align="right", mono=True),
    Column(key="arrive", label="도착", mono=True),
]
_PAY_COLS = [
    Column(key="leg", label="회차"),
    Column(key="buy", label="사는 날", mono=True),
    Column(key="pay", label="내는 날", mono=True),
    Column(key="amount", label="금액", align="right", mono=True),
]
_COMMITTED_COLS = [
    #  🔴 칸 이름이 틀렸었다 — 「승인」인데 실린 값은 **매입 번호**(`purchase_id`)다 (2026-09-17).
    #     화면은 이 칸을 **가린다** (`console/purchase/page.tsx`) · 값은 API 에 남긴다.
    Column(key="approval", label="매입 번호", mono=True),
    Column(key="item", label="품목"),
    Column(key="buy", label="사는 날", mono=True),
    Column(key="arrive", label="도착", mono=True),
    Column(key="grade", label="등급"),
    Column(key="qty", label="수량", align="right", mono=True),
    Column(key="unit", label="단가", align="right", mono=True),
    Column(key="amount", label="금액", align="right", mono=True),
    Column(key="pay", label="지급", mono=True),
]

#: 축 어휘를 사람 말로. **내부 단어를 화면에 내보내지 않는다.**
_KNOB = {
    "quantity": "수량으로 조절",
    "timing": "사는 시점으로 조절",
    "mix": "등급 구성으로 조절",
}

#: 빈 표 안내. 🔴 **여기서 둘을 같이 고쳤다** (2026-09-10).
#:
#:   ~~"위에서 안을 고르면"~~   ① «위에서» 가 틀렸다 — **이 탭에 승인 버튼이 없다.**
#:                              승인은 서랍(마스터)에서 한다 (page.tsx 머리말이 그 이유를
#:                              적는다: 두 군데서 승인하면 «어느 쪽으로 승인했나» 가
#:                              기록에서 갈린다). 읽는 사람을 없는 버튼으로 보낸다
#:                            ② «고르면» 이 곧 거짓이 된다 — 걷기 구간을
#:                              `decided_by="AUTO-BACKFILL"` 로 채우기로 정해졌다
#:                              (마스터 통보 「백필 승인은 사람 승인이 아닙니다」)
_EMPTY_COMMITTED = "아직 확정된 매입이 없습니다 — 안이 승인되면 여기에 생깁니다"

#: 한 번에 사는 안의 빈 지급 표. 예시값도 같은 문장을 쓴다 — 두 곳에 적으면 한쪽만
#: 낡는다 (전에는 둘 다 「지급일 규칙이 아직 미결」이었고 같이 틀렸다 · ``_payments`` 참조).
_PAY_EMPTY_SINGLE = "한 번에 사는 안이라 지급 계획을 따로 만들지 않습니다"



# ══════════════════════════════════════════════════════════════════════════
#  실행 고르기 — 🔴 레슨 ①
# ══════════════════════════════════════════════════════════════════════════

def _pick(
    runs: list[dict[str, Any]], sim_run_id: str | None = None
) -> tuple[list[dict[str, Any]], str]:
    """품목마다 **하나씩** 고르고, 몇 개 중 무엇을 골랐는지 같이 돌려준다.

    🔴 **같은 날 실행이 여럿이다.** `2026-01-06` 배추는 아홉이고 그중 넷이
    승인이다. 아무 말 없이 하나를 고르면, 다음 사람이 다른 행을 보고 «값이
    다르다» 고 한다. 그래서 규칙을 코드에 박고 화면에 적는다.

    ::

        ① runtime_status = 'READY'   — 미가동(E4)은 안을 못 낸 날이다
        ② scenarios 가 비지 않은 것
        ③ 🔴 item 이 계약 품목일 것 (contracts.core.ITEMS)
        ④ 🔴 sim_run_id 가 그 축일 것 — **안 주면 안 거른다**
        ⑤ 품목별 created_at 최신 하나

    🔴 **④ 가 ⑤ 앞이어야 한다.** 뒤로 가면 «최신 하나» 가 먼저 다른 걷기의 행을 집고
    그 뒤에 축으로 떨어뜨려, 같은 축에 있던 조금 오래된 행이 **같이 사라진다.**

    ⚠️ 지금 DB 에서는 축 있는 행이 언제나 더 새것이라(축이 `2026-09-08` 에 생겼다)
    순서를 바꿔도 값이 안 갈린다 — 그래서 **검사가 상황을 주입한다** (규칙 8).

    🔴 **축 이름을 쪼개 뜻을 읽지 않는다.** `SIM-WALK-202601-BASE` 의 `BASE` 는 사람이
    목록에서 고를 때 쓰는 꼬리표이고, 뜻은 `sim_runs` 행이 답한다 (마스터 통보
    2026-09-10). 여기서는 **같은지만** 본다.

    🔴 **③ 이 없으면 화면에 계약 밖 품목이 뜬다.** 저장된 실행에 피마늘 행이
    **194건** 남아 있다 (2026-09-09 실측 · 종전 주석의 143건은 그 뒤 늘었다)
    — `#216` 으로 계약에서 뺐지만 **기록은 일부러 안 고쳤다**
    (`e63f990` *"고쳐 쓰면 기록이 거짓이 된다"*). 기록을 고칠 자리가 아니라
    **보일 때 거를 자리**다. 계약이 그렇게 적어 두었다::

        contracts/core.py  ITEMS 각주
        제안 축   "사자고 제안한 품목"   ITEMS 로 거른다
        재고 축   "창고에 있는 품목"     자유 문자열 — 좁히지 않는다

    이 화면은 **제안 축**이다.

    ⚠️ 거른 것을 조용히 없애지 않는다 — 몇 건을 왜 뺐는지 돌려주는 글에 적는다.
    """
    ready = [r for r in runs if r["runtime_status"] == "READY"]
    with_plans = [r for r in ready if (r["payload"] or {}).get("scenarios")]
    ours = [r for r in with_plans if r["item"] in ITEMS]
    dropped = sorted({str(r["item"]) for r in with_plans if r["item"] not in ITEMS})

    #  ④ 축. `is None` 이라야 한다 — 빈 문자열은 «안 줬다» 가 아니라 **잘못 준 것**이고,
    #     그것을 «전부» 로 읽으면 오타가 조용히 전체 조회가 된다.
    mine = ours if sim_run_id is None else [r for r in ours if r["sim_run_id"] == sim_run_id]
    off_axis = len(ours) - len(mine)

    picked: dict[str, dict[str, Any]] = {}
    for run in mine:  # 이미 created_at DESC 라 처음 만난 것이 최신이다
        picked.setdefault(str(run["item"]), run)
    chosen = list(picked.values())

    aside = ""
    if dropped:
        names = " · ".join(x if x != "None" else "품목 미상" for x in dropped)
        aside = f". 계약 밖 품목({names})은 뺐습니다 — 지금 사는 것은 {'·'.join(ITEMS)} 입니다"
    #  🔴 거른 것을 조용히 없애지 않는다 — 계약 밖 품목과 같은 규율이다.
    if off_axis:
        #  🔴 **뺀 건수만 적는다** (2026-09-17). 어느 걷기를 보는지는 `build` 가 안 목록
        #     안내 끝에 **한 번만** 적는다 — 여기서 또 적으면 같은 이름이 두 번 나온다.
        aside += f". 다른 걷기의 실행 {off_axis}건은 뺐습니다"
    #  🔴 **안 거를 때도 말한다** (마스터 청구 2026-09-10). 축이 없는 실행은 손으로
    #     돌린 것이거나 축이 생기기 전 기록인데, 걷기와 **같아 보이면** 보는 사람이
    #     둘을 한 세상으로 읽는다. 마스터가 실제로 그 오독을 했다 —
    #     *"같은 토요일인데 하나는 0건이고 하나는 4건이니 걷는 경로가 둘이다"* 로
    #     진단했다가 물렀고, 실은 표에 손 실행이 섞여 있었을 뿐이었다.
    outside = sum(1 for r in chosen if r["sim_run_id"] is None)
    if outside:
        aside += (
            f". 보이는 것 중 {outside}건은 **걷기 밖 실행**입니다 —"
            " 손으로 돌렸거나 걷기 축이 생기기 전 기록입니다"
        )
    if not chosen:
        return [], f"그날 실행 {len(runs)}건 · 그중 안을 낸 계약 품목 실행 0건{aside}"
    #  🔴 **요청 ID 를 글에 안 싣는다** (2026-09-17). 무엇을 골랐는지는 「품목별 최신 하나」
    #     라는 **규칙 문장**이 말하고(레슨 ①), 어느 실행인지는 안마다 `request_id` ·
    #     `history_run_id` 칸에 남는다 — 말로 한 승인이 그 칸을 쓴다.
    #  ★ 「환경이 선 것」은 `runtime_status=READY` 의 안쪽 말이라 풀어 쓴다. 수는 그대로다.
    names = " · ".join(str(r["item"]) for r in chosen)
    return chosen, (
        f"그날 실행 {len(runs)}건 중 {len(ready)}건이 돌았고 "
        f"{len(with_plans)}건이 안을 냈습니다 — 품목별 최신 하나를 보입니다 ({names}){aside}"
    )


def _current_decisions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """업무 키마다 **지금 유효한 결정 하나** — 최대 `decision_seq` 행 (2026-09-17).

    🔴 **마스터와 같은 규칙이다.** `master/pending_transition_repository.approved_decisions`
    가 `DISTINCT ON (request_id) … ORDER BY decision_seq DESC` 로, `decision.mark_current`
    가 파이썬에서 같은 일을 한다. 결정 표는 append-only 라 번복도 새 행이다.

    ⚠️ 전에는 순서를 안 봐서, 승인 뒤 「승인 되돌리기」(`REQUEST_CHANGE` · 안 이름 없음)를
    적어도 옛 `APPROVE` 행이 남아 **「승인됨」 · 「매입 기록됨」으로 떴다.** 실측 — FINAL-0918
    09-14 배추(`REQ-20260914-0001` · 05:50 승인 → 06:08 되돌림)가 「매입 기록됨」이었다.
    같은 요청에 안을 바꿔 여러 번 승인한 경우도 **전부** 「승인됨」이었다.

    ★ 되돌린 요청은 안 이름 붙은 유효 결정이 없으므로 「후보」 · 대기로 돌아간다 —
      낱말은 `master/domain/plan_state.py` 가 정하고 여기서 새로 만들지 않는다.

    ⚠️ 검사 대역은 `decision_seq` 를 안 넣기도 한다 — 그때는 **목록 순서**를 회차로 읽는다
      (뒤에 온 행이 새것). 실 조회는 늘 그 칸을 싣는다.
    """
    current: dict[Any, tuple[Any, dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        seq = row.get("decision_seq")
        order = index if seq is None else seq
        kept = current.get(row["request_id"])
        if kept is None or order >= kept[0]:
            current[row["request_id"]] = (order, row)
    return [row for _order, row in current.values()]


def _no_plan_note(
    runs: list[dict[str, Any]], picked_text: str, sim_run_id: str | None = None
) -> Note:
    """안이 왜 없나. 🔴 레슨 ② — ``no_proposal_reason`` 이라는 칸은 **없다.**

    ``reason`` 은 한 줄 요약이라 *"어느 안이 왜 죽었나"* 를 못 말한다. 안별 컷
    사유는 ``judgment.rejected_reasons[]`` 에 있다.

    🔴 **사유도 같은 축에서만 가져온다** (2026-09-10). 안 그러면 «축으로 걸러 0건» 인데
    화면이 **다른 걷기의 컷 사유**를 붙여 *"단가가 상한을 넘어 죽었다"* 고 말한다 —
    실제로는 그 걷기에 실행이 아예 없었던 것이다. 「안 돌았다」와 「돌았는데 죽었다」는
    다른 사실이고, 섞으면 읽는 사람이 없는 원인을 고치려 든다.
    """
    if sim_run_id is not None:
        runs = [r for r in runs if r["sim_run_id"] == sim_run_id]
        if not runs:
            return Note(
                tone="warn",
                text=f"{picked_text}. 이 걷기의 실행이 그날 없습니다 —"
                " 안이 죽은 것이 아니라 돌지 않았습니다.",
            )
    for run in runs:
        payload = run["payload"] or {}
        rejected = (payload.get("judgment") or {}).get("rejected_reasons") or []
        if rejected:
            lines = " · ".join(f"**{x.get('label')}** {x.get('reason')}" for x in rejected)
            return Note(tone="warn", text=f"{picked_text}. 안별 컷 사유 — {lines}")
    for run in runs:
        reason = (run["payload"] or {}).get("reason")
        if reason:
            return Note(tone="warn", text=f"{picked_text}. {reason}")
    return Note(tone="warn", text=f"{picked_text}. 사유를 남긴 실행이 없습니다.")


# ══════════════════════════════════════════════════════════════════════════
#  시나리오 → 화면
# ══════════════════════════════════════════════════════════════════════════

def _money(value: Any) -> int | None:
    return None if value is None else round(float(value))


def _sourcing(lines: list[dict[str, Any]]) -> tuple[str, int] | None:
    """등급과 단가. **가중평균으로 접지 않는다** (판매 요청 · `§15-6`).

    ★ 등급이 여럿이면 **가장 비싼 단가**를 보인다. 컷은 줄마다 걸리므로
      (``self_check.check_max_price``), 컷 기준과 견줄 값은 최고가다.
      그 사실을 등급 글자에 그대로 적는다 — 숨기면 평균으로 읽힌다.
    """
    if not lines:
        return None
    prices = [round(float(line["grade_unit_price"])) for line in lines]
    grades = list(dict.fromkeys(str(line["grade"]) for line in lines))
    if len(lines) == 1:
        return grades[0], prices[0]
    return f"{' · '.join(grades)} ({len(lines)}등급 · 최고가 표시)", max(prices)


def _legs(scenario: dict[str, Any]) -> Table:
    rows: list[dict[str, Any]] = []
    for leg in scenario.get("split_plan") or []:
        qty = leg.get("qty_kg")
        rows.append({
            "leg": leg.get("seq"),
            "buy": leg.get("date"),
            "qty": None if qty is None else f"{float(qty):,.0f} kg",
            "arrive": leg.get("expected_arrival_date"),
        })
    return Table(columns=_LEG_COLS, rows=rows, empty_text="이 안에는 회차 계획이 없습니다")


def _payments(scenario: dict[str, Any]) -> Table:
    """지급 계획. ★ **분할 안에서만 실린다.**

    저장된 실행에서 회차 수와 완전히 맞물린다 (2026-09-08 실측)::

        split_plan 1회차   719건   payment_schedule 없음
        split_plan 2회차   228건   payment_schedule 배열     ← 228 = 228

    ⚠️ 그래서 **빈 표가 흔한 것이 정상**이다. 한 번에 사는 안은 지급이 한 건이라
    따로 계획을 만들지 않는다 (``package_scenarios.build_payment_schedule``).
    없는 것을 매입일로 메우지 않는다 (규칙 3).

    🔴 ~~지급일 규칙(N5)이 아직 미결이라 더 그렇다~~ — **낡았다** (2026-09-17 정정).
    재무가 N5=0 을 `2026-09-10` 에 확정했고, 안을 낸 실행은 전부 그 값을 받았다
    (REH-0914 378/378 · FINAL-0918 505/505). 그런데도 1회차 줄을 안 만드는 이유는
    **두 벌**이다 — 만들면 ``split_plan[].amount_krw`` 와 같은 값이 한 번 더 나간다.
    빈 표 문구도 그래서 「미결」이 아니라 「한 번에 사는 안」을 말한다.

    ⚠️ **나눠 사는 안인데 계획이 없는 경우는 다른 문장이다.** 에이전트는 N5 를 못
    받았을 때도 계획을 안 만든다. 그 자리에 「한 번에 사는 안」을 적으면 거짓이고,
    원인을 짐작해 적는 것도 짓는 것이라 **실리지 않았다는 사실만** 적는다.

    ``basis`` · ``amount_max_krw`` 는 안 싣는다. 앞은 내부 어휘이고 뒤는 **재무
    STRESS 금액**이라, 지급 표에 두면 실제로 낼 돈으로 읽힌다.
    """
    rows: list[dict[str, Any]] = []
    for pay in scenario.get("payment_schedule") or []:
        amount = pay.get("amount_krw")
        rows.append({
            "leg": pay.get("seq"),
            "buy": pay.get("purchase_date"),
            "pay": pay.get("payment_date"),
            "amount": None if amount is None else f"{_money(amount):,} 원",
        })
    return Table(
        columns=_PAY_COLS,
        rows=rows,
        #  🔴 N5 값이나 사는 날을 문장에 넣지 않는다 — 회차 표에 이미 있는 사실이다.
        empty_text=(
            _PAY_EMPTY_SINGLE
            if len(scenario.get("split_plan") or []) <= 1
            else "나눠 사는 안인데 지급 계획이 실리지 않았습니다"
        ),
    )


#  ── 안 이름 「품목 · 안 이름」 읽기 ─────────────────────────────────────────
#
#  🟢 **자리 (2026-09-29 · 재구성 BL-012).** 전에는 `app/api/plan_state.py` 에 상태 판정과
#     함께 있었다. 판정(`state_of`)과 낱말은 마스터 domain(`app/master/domain/
#     plan_state.py`)으로 갔고, 이 셋은 **이 파일의 `_plan` 이 짓는 이름**을 거꾸로 쪼개
#     읽는 일이라 이름을 짓는 자리 옆에 남겼다. 대시보드도 여기 것을 부른다.


def plan_item(key: str) -> str | None:
    """안의 품목. 🔴 매입 `Plan` 스키마에 품목 칸이 없다 (2026-09-14 확인).

    매입 `_plan()` 이 `key=f"{item} · {label}"` 로 품목을 이름 앞에 넣는다. 그 앞자리를
    **계약 품목(`ITEMS`)과 맞춰** 읽는다 — 이름을 코드에 박지 않고, 계약 밖이면 공란.
    매입 스키마에 품목 칸이 서는 날 이 함수를 그 칸 읽기로 바꾼다.
    """
    return next((item for item in ITEMS if key.startswith(f"{item} · ")), None)


def plan_label(key: str) -> tuple[str, str] | None:
    """안 이름을 `(품목, 안 이름)` 으로 가른다 — 실매입 기록을 맞출 열쇠다.

    ★ `plan_item` 과 **같은 규칙**을 쓴다 (`key=f"{item} · {label}"`). 계약 밖 품목이면
      `None` 이고, 그러면 기록도 안 맞춘다 — 지금 사는 품목이 아니다.
    """
    item = plan_item(key)
    return None if item is None else (item, key[len(item) + len(" · ") :])


def recorded_for(
    records: dict[tuple[str, str], RecordedTotals], key: str
) -> RecordedTotals | None:
    """이 안에 적힌 실매입. 🔴 **열쇠가 `(품목, 안 이름)` 둘 다**여야 한다.

    품목만 맞추면 같은 품목의 다른 안(보수 · 기본 · 공격)에 **엉뚱한 기록**이 붙는다.
    """
    pair = plan_label(key)
    return None if pair is None else records.get(pair)


def _records(as_of: date, sim_run_id: str | None) -> dict[tuple[str, str], RecordedTotals]:
    """그날 · 그 축의 **실매입 기록 합계.** 열쇠는 `(품목, 안 이름)`.

    🔴 **여기서 숫자를 만들지 않는다.** 표를 읽고 합계를 엮는 자리는 마스터 한 곳이고
       (`master/readmodel/purchase_record.recorded_totals_by_plan` — SQL 은 그 아래
       repository) 이 함수는 그것을 부르기만 한다 — 대시보드(`api/dashboard/query.
       _records`)와 **같은 함수**다.

    🔴 **축이 없으면 안 맞춘다.** 그 조회는 `sim_run_id` 가 필수다 (PK 에 축이 있다).
       아무 축이나 넣어 맞추면 **다른 걷기에서 산 값**이 이 안에 붙는다 — 틀린 줄도
       모르는 오류다. 그때는 빈 표이고, 승인된 안은 「승인됨」에 머문다.

    ⚠️ 못 읽으면 **빈 표**다. 이 탭은 DB 를 못 읽어도 떠야 하고, 기록 하나 때문에
      안 목록이 통째로 죽으면 안 된다 (`build` 의 태도와 같다).
    """
    if sim_run_id is None:
        return {}
    try:
        return recorded_totals_by_plan(sim_run_id=sim_run_id, as_of=as_of)
    except Exception as error:  # noqa: BLE001  DB 미연결 · 표 없음 둘 다
        log.info("실매입 기록을 못 읽어 안의 상태를 승인 여부까지만 적습니다: %s", error)
        return {}


def _plan(item: str, scenario: dict[str, Any], decided: dict[tuple[str, str], str],
          request_id: str, sim_run_id: str | None = None, *,
          run_id: Any = None,
          records: dict[tuple[str, str], RecordedTotals] | None = None,
          decided_requests: frozenset[str] = frozenset()) -> Plan | None:
    label = str(scenario.get("label") or "")
    sourcing = _sourcing(scenario.get("sourcing_plan") or [])
    if sourcing is None:
        return None
    grade, unit_price = sourcing
    decision = decided.get((request_id, label))
    coverage = scenario.get("coverage_days")
    key = f"{item} · {label}"
    return Plan(
        #  🔴 품목을 이름에 넣는다. 이 탭에는 품목 축이 없는데 우리는 품목마다
        #     따로 도므로, 안 넣으면 여러 품목이 있는 날 이름이 겹친다.
        key=key,
        coverage="며칠치인지 모름" if coverage is None else f"{coverage}일치",
        knob=_KNOB.get(str(scenario.get("strategy_type")), "조절 축을 알 수 없음"),
        qty_kg=float(scenario.get("total_qty_kg") or 0),
        amount_krw=_money(scenario.get("total_amount_krw")) or 0,
        unit_price=unit_price,
        grade=grade,
        max_price=_money(scenario.get("max_price")) or 0,
        #  🔴 없으면 None 이다. max_price 로 대신 채우지 않는다 — 그 순간
        #     갈라 둔 두 값이 화면에서 다시 하나가 된다.
        cut_unit_price=_money(scenario.get("cut_unit_price")),
        legs=_legs(scenario),
        payments=_payments(scenario),
        reasons=[
            Reason(
                source=str(r.get("source") or "출처 미상"),
                text=str(r.get("claim") or ""),
                ref=r.get("ref_id"),
            )
            for r in scenario.get("rationale") or []
        ],
        risks=[str(x) for x in scenario.get("risks") or []],
        #  🔴 **「대기」는 안이 아니라 요청(품목·날)의 사실이다** (2026-09-17).
        #     전에는 `decision is None` — (요청, 안 이름) 한 쌍으로만 봐서, 같은 요청에서
        #     보수안이 승인되면 **고르지 않은 기본안이 「승인 대기」로 남았다.** 매입 탭 통계와
        #     대시보드 배지가 그 수를 셌다 (REH-0914 08-31 · 3건인데 기다리는 것은 양파 1건).
        #  ★ 낱말(`state` 「후보」)은 안 바꾼다 — 주인은 `master/domain/plan_state.py` 다. 형제 안은
        #    「후보」 그대로이고, **기다리는 수에서만** 빠진다.
        pending=request_id not in decided_requests,
        approved=decision == "APPROVE",
        #  🔴 **`approved` 하나로는 못 가른다** — 승인만 된 안과 실매입까지 적은 안이
        #     둘 다 참이다. 낱말과 판정의 주인은 `app/master/domain/plan_state.py` 하나이고
        #     (대시보드도 같은 것을 쓴다) 여기서는 부르기만 한다.
        state=plan_state.state_of(
            approved=decision == "APPROVE",
            recorded=recorded_for(records or {}, key) is not None,
        ),
        #  🔴 말로 한 승인이 이 둘을 짚어 `/master/ask/execute` 에 싣는다.
        #     **못 읽으면 None 이다** — 지어내면 엉뚱한 실행이 승인된다.
        request_id=request_id,
        history_run_id=None if run_id is None else str(run_id),
        #  🔴 없으면 None 그대로 싣는다. «걷기 밖» 이라는 사실이고, 화면이 그것을
        #     보일 수 있어야 한다 (schema.Plan.sim_run_id 주석).
        sim_run_id=sim_run_id,
    )


# ══════════════════════════════════════════════════════════════════════════
#  확정 매입
# ══════════════════════════════════════════════════════════════════════════

def _arrival_index(
    arrivals: list[dict[str, Any]], sim_run_id: str | None = None
) -> dict[tuple[date, str, int], str]:
    """(매입일, 품목, 줄금액) → 도착일. **금액으로 맞춘다.**

    🔴 축을 주면 그 걷기의 실행에서만 맞춘다. 안 그러면 다른 걷기가 우연히 같은 금액을
    낸 날에 **엉뚱한 도착일**이 붙고, 그건 틀린 줄도 모르는 오류다.

    ⚠️ **축을 걸면 지금 맞던 줄이 공란이 된다** (2026-09-10 실측). `01-22` 까지 원장
    네 줄 중 **둘**이 축 없는 실행에서 도착일을 받아 오고 있었다::

        2026-01-05 배추   원장축 SIM-BURNIN-202512 ← 도착일 출처축 없음
        2026-01-13 배추   원장축 SIM-BURNIN-202512 ← 도착일 출처축 없음

    ★ **공란이 맞다.** 금액으로 맞추는 것은 원래 추정이고, 축이 다르면 그 추정을 받칠
    근거가 없다. 화면은 «도착일을 못 맞춘 줄 N개는 공란» 이라고 이미 적는다 — 없는
    값을 지어내는 것보다 못 맞췄다고 말하는 편이 낫다.
    """
    index: dict[tuple[date, str, int], str] = {}
    for row in arrivals:
        if sim_run_id is not None and row["sim_run_id"] != sim_run_id:
            continue
        for scenario in row["scenarios"] or []:
            amount = _money(scenario.get("total_amount_krw"))
            if amount is None:
                continue
            for leg in scenario.get("split_plan") or []:
                arrive = leg.get("expected_arrival_date")
                if arrive:
                    index.setdefault((row["as_of"], row["item"], amount), arrive)
    return index


def _committed(
    data: dict[str, Any], as_of: date, sim_run_id: str | None = None
) -> tuple[Table, int, list[float], int]:
    """확정 매입 표 · 이번 주 금액 · 아직 안 온 물량 · **다른 걷기라 뺀 줄 수.**

    🔴 원장도 축을 따른다. 안 그러면 이번 주 매입액이 **두 세상의 합**이 된다.
    """
    index = _arrival_index(data["arrivals"], sim_run_id)
    names = data["items"]
    week_start = as_of - timedelta(days=as_of.weekday())
    rows: list[dict[str, Any]] = []
    week_amount = 0
    inbound: list[float] = []
    off_axis = 0
    for buy in data["buys"]:
        if sim_run_id is not None and buy["sim_run_id"] != sim_run_id:
            off_axis += 1
            continue
        item = names.get(buy["item_id"], buy["item_id"])
        #  🔴 레슨 ③ — 줄 금액은 line_amount_krw 다. total 을 쓰면 여러 줄인
        #     매입에서 같은 금액이 줄마다 반복된다.
        amount = _money(buy["line_amount_krw"])
        arrive = index.get((buy["purchase_date"], item, amount))
        qty = float(buy["quantity_kg"])
        rows.append({
            "approval": buy["purchase_id"],
            "item": item,
            "buy": buy["purchase_date"].isoformat(),
            "arrive": arrive,  # 못 맞추면 공란. 0 도 오늘도 아니다
            "grade": buy["grade"],  # 원장이 NULL 이면 공란
            "qty": f"{qty:,.0f} kg",
            "unit": f"{_money(buy['unit_price_krw_per_kg']):,}",
            "amount": f"{amount:,}" if amount is not None else None,
            "pay": buy["payment_due_date"].isoformat() if buy["payment_due_date"] else None,
        })
        if amount is not None and week_start <= buy["purchase_date"] <= as_of:
            week_amount += amount
        if arrive and date.fromisoformat(arrive) > as_of:
            inbound.append(qty)
    return (
        Table(columns=_COMMITTED_COLS, rows=rows, empty_text=_EMPTY_COMMITTED),
        week_amount,
        inbound,
        off_axis,
    )


# ══════════════════════════════════════════════════════════════════════════
#  예시값 — DB 를 못 읽을 때만
# ══════════════════════════════════════════════════════════════════════════

_DEMO_REASONS = [
    Reason(source="예측", text="D+14 예측 −2.8%, 신뢰구간 폭 63.9%", ref="FC-2026-01-06"),
    Reason(source="시세관측", text="가락 2026-01-05 경락가 925원/kg 등 1개 등급",
           ref="MQ-가락-2026-01-05"),
    Reason(source="주문", text="확정주문 10,042.2kg → 일평균 717kg", ref="SO-2026-01-06"),
    Reason(source="재고", text="가용 286.92kg (로트 LOT-KIMCHI-015)", ref="INV-0106"),
    Reason(source="현금", text="재무 매입 상한 13,057,049원까지 매입 가능", ref="CASH-0106"),
]

#: 🔴 **문서 줄은 ⑥이 만드는 문장을 베낀 것이다** — 두 자리가 갈리면 예시값이 실물과
#:   다른 말을 한다. 원본은 ``package_scenarios.context_risks`` 이고, 문면을 고칠 때
#:   **여기를 같이 본다** (2026-09-09 · E3-5).
#:
#:   전에는 *"참조 가능한 발간물 0건"* 이었는데 **그건 다른 상태의 문장**이다. 운영은
#:   문서를 읽으려다 못 읽는 쪽이라 예시도 그쪽을 보여야 한다 — 실물 기록 158건이
#:   그 문장이었고, 읽는 사람에게는 *"그날 그 문서가 세상에 없었다"* 로 읽혔다.
_DEMO_RISKS = [
    "기존 로트 LOT-KIMCHI-015 잔여신선도 4일 — 새로 사는 물량이 이 로트를 밀어내지 않는지",
    "기준등급 ‘상’이 당일 시세에 없어 ‘특’으로 배정했다",
    "문서 1종을 요청했으나 읽지 못했다 — 그날 발간물이 0건이었다는 뜻이 아니다",
]


def _demo_plan(label: str, coverage: str, qty: float, amount: int, cap: int) -> Plan:
    return Plan(
        key=f"배추 · {label}", coverage=coverage, knob="수량으로 조절",
        qty_kg=qty, amount_krw=amount, unit_price=925, grade="특",
        max_price=cap, cut_unit_price=cap,
        legs=Table(columns=_LEG_COLS, rows=[
            {"leg": 1, "buy": "2026-01-06", "qty": f"{qty:,.0f} kg", "arrive": "2026-01-08"},
        ]),
        payments=Table(columns=_PAY_COLS, rows=[],
                       empty_text=f"예시값입니다 — {_PAY_EMPTY_SINGLE}"),
        reasons=list(_DEMO_REASONS), risks=list(_DEMO_RISKS), pending=True,
    )


def _demo(note: str) -> PurchaseTab:
    plans = [
        _demo_plan("보수", "2일치", 1435, 1_327_375, 1095),
        _demo_plan("기본", "5일치", 3587, 3_318_475, 1095),
    ]
    return PurchaseTab(
        stats=[
            Stat(label="오늘 제안", value="2", unit="안", detail="예시값", raw=2),
            Stat(label="승인 대기", value="2", unit="건", detail="예시값", tone="warn", raw=2),
            Stat(label="이번 주 확정 매입액", value="3,063,298", unit="원",
                 detail="예시값", tone="warn", raw=3_063_298),
            Stat(label="확정 입고 예정", value="3,587", unit="kg",
                 detail="예시값", tone="good", raw=3587),
        ],
        plans=plans,
        plans_note=Note(tone="warn", text=f"**예시값입니다.** {note}"),
        committed=Table(columns=_COMMITTED_COLS, rows=[], empty_text=_EMPTY_COMMITTED),
        committed_note=Note(tone="warn", text="**예시값입니다.** 확정 매입을 못 읽었습니다."),
        source=Source(filled=False, owner="매입", note=note),
    )


# ══════════════════════════════════════════════════════════════════════════
#  본체
# ══════════════════════════════════════════════════════════════════════════

def build(
    as_of: date, sim_run_id: str | None = None, window_days: int | None = None
) -> PurchaseTab:
    """매입 탭. ``sim_run_id`` 는 **어느 걷기를 보는가**다.

    🔴 **안 주면 안 거른다.** 지금 DB 에는 축이 붙기 전 실행이 1,202건 있고, 그것을
    무조건 걸러 버리면 스무 날이 통째로 빈다 (2026-09-10 실측). 축이 갈리는 날 화면이
    두 세상을 섞지 않도록 **자리를 먼저 만들어 두는 것**이 이 인자다.

    ⚠️ 값을 여기서 짓지 않는다 — 받아서 그대로 흘린다 (마스터 당부 2026-09-10).

    🔵 ``window_days`` 는 도착일 조회의 날짜 창이고 뜻은 ``read_purchase_tab`` 이 적는다
    (2026-09-16). **매입 탭 자신은 안 넘긴다** — 이 인자는 이 함수를 재사용하는
    대시보드를 위한 자리다.

    🔴 **좁히면 「확정 입고 예정」을 숫자로 안 적는다.** 그 칸은 도착일에서 나오는데,
    창을 좁히면 도착일이 비어 합계가 ``0`` 이 된다. 그건 «확정된 0» 이 아니라
    «안 읽었다» 라 규칙 3 위반이다 — **미결로 낸다.**
    """
    #  ★ 통째로 잡는 것이 맞습니다 — 여기서 무슨 일이 나든 **화면은 떠야** 하고
    #    대신 「예시값」 딱지가 붙습니다. 예외 종류를 골라 잡으면 안 골라낸
    #    하나 때문에 화면이 통째로 죽습니다 (ML 이 forecast 에서 같은 판단).
    try:
        #  🔴 축을 흘린다 — 안 흘리면 도착일 조회가 모든 걷기를 다시 끌어온다.
        #     `tests/api/test_purchase_tab_axis_sql.py` 가 이 자리를 직접 잠근다.
        data = read_purchase_tab(as_of, window_days=window_days, sim_run_id=sim_run_id)
    except Exception as error:  # noqa: BLE001  DB 미연결 · 표 없음 둘 다
        log.info("매입 값을 못 읽어 예시값을 씁니다: %s", error)
        return _demo(f"DB 를 못 읽었습니다 ({type(error).__name__})")

    #  🔴 **기본이 「전부 읽었다」다.** 검사가 `read_purchase_tab` 을 대신 세울 때 이 칸을
    #     안 넣는데, 그 주입은 자기가 준 `arrivals` 가 전부인 세상이라 «온전» 이 맞다.
    #  ⚠️ 위 `except` 가 **통째로 잡는다** — 그래서 `read_purchase_tab` 시그니처가 안 맞으면
    #     `TypeError` 도 삼켜져 조용히 예시값이 나간다. 스텁을 넓힐 때 그것이 실제
    #     위험이었고, `tests/api/test_purchase_tab_window.py` 가 그 자리를 지킨다.
    arrivals_complete = data.get("arrivals_complete", True)
    runs = data["runs"]
    decided = {
        (row["request_id"], row["scenario_label"]): row["decision"]
        for row in _current_decisions(data["decisions"])
        if row["scenario_label"]
    }
    #  ★ 결정이 난 요청. `decided` 와 **같은 행**에서 뽑는다 — 무엇을 결정으로 치는지
    #    (안 이름이 붙은 행)가 두 곳에서 갈리면 「승인됨」과 「대기 아님」이 따로 논다.
    decided_requests = frozenset(request_id for request_id, _label in decided)
    chosen, picked_text = _pick(runs, sim_run_id)
    #  ★ 안과 **같은 실행 · 같은 날**의 실매입 기록. 열쇠는 `(품목, 안 이름)`.
    records = _records(as_of, sim_run_id)

    plans: list[Plan] = []
    skipped = 0
    for run in chosen:
        for scenario in (run["payload"] or {}).get("scenarios") or []:
            #  _pick 이 ITEMS 로 걸렀으므로 여기서 item 은 언제나 계약 품목이다
            plan = _plan(
                str(run["item"]), scenario, decided, run["request_id"], run["sim_run_id"],
                #  ⚠️ `.get` 이다. 검사가 `read_purchase_tab` 을 대신 세울 때 이 칸을 안 넣는데,
                #     그때 «못 읽었다» 로 두는 것이 맞다 — 지어내지 않는다.
                run_id=run.get("run_id"),
                records=records,
                decided_requests=decided_requests,
            )
            if plan is None:
                skipped += 1
            else:
                plans.append(plan)

    committed, week_amount, inbound, committed_off_axis = _committed(data, as_of, sim_run_id)
    week_start = as_of - timedelta(days=as_of.weekday())
    pending = sum(1 for p in plans if p.pending)
    no_cut = [p.key for p in plans if p.cut_unit_price is None]

    if plans:
        text = picked_text
        #  🔴 **어느 걷기를 보는지는 이 한 자리에만 적는다** (2026-09-17). 실행 이름은 내부
        #     식별자라 한 번 걷었다가, 화면에 어디에도 안 남아 되살렸다 — 두 세상을 섞어
        #     읽지 않게 하는 것이 09-10 에 이 이름을 실은 이유다. 요청 ID 는 여전히 안 싣는다.
        #  ⚠️ 확정 매입 안내 · 안 없는 날 안내에는 **안 넣는다** — 한 곳이면 된다.
        if sim_run_id is not None:
            text += f". 이 걷기({sim_run_id})를 봅니다"
        if skipped:
            text += f". 등급 배분이 비어 화면에 못 올린 안 {skipped}개"
        if no_cut:
            #  🔴 09-08 에 갈라 둔 칸이 옛 실행에는 없다. max_price 로 메우면
            #     두 값이 화면에서 다시 하나가 된다.
            text += (
                f". ⚠️ 컷 기준 칸이 없는 안 {len(no_cut)}개 — 이 실행보다 그 칸이"
                " 나중에 생겼습니다. 재무 스트레스 기준으로 대신 읽지 마세요"
            )
        plans_note = Note(tone="neutral", text=text)
    else:
        plans_note = _no_plan_note(runs, picked_text, sim_run_id)

    arrived_unknown = sum(1 for row in committed.rows if row["arrive"] is None)
    committed_text = (
        #  🔴 `MASTER_APPROVAL` 은 원장의 안쪽 코드다 — 뜻만 적는다 (2026-09-17).
        "승인을 거쳐 원장에 남은 매입만 봅니다 — "
        f"{as_of.isoformat()} 까지 {len(committed.rows)}줄."
    )
    if not arrivals_complete:
        #  🔴 **「못 맞췄다」와 「안 읽었다」를 다른 문장으로 적는다.** 아래 문장은
        #     *"맞출 것을 다 보고도 못 맞췄다"* 는 뜻이라, 창을 좁힌 날 그 말을 쓰면
        #     읽는 사람이 없는 원인을 고치려 든다.
        #  ★ E3-5 에서 같은 판단을 했다 — 「못 읽었다」와 「발간물 0건」을 갈랐다.
        committed_text += (
            f" 도착일은 이 호출에서 **안 읽었습니다** (날짜 창 {window_days}일) —"
            " 공란이 «못 맞췄다» 라는 뜻이 아닙니다."
        )
    elif arrived_unknown:
        committed_text += f" 도착일을 못 맞춘 줄 {arrived_unknown}개는 **공란**입니다."
    #  🔴 뺀 것을 조용히 없애지 않는다 — 이번 주 매입액이 왜 작은지가 여기 있다.
    if committed_off_axis:
        #  🔴 뺀 수는 남기고 실행 이름은 안 적는다 — `_pick` 의 안내문과 같은 규율이다.
        committed_text += f" 다른 걷기의 줄 {committed_off_axis}개는 뺐습니다."
    #  🔴 ~~「⚠️ 지급일이 매입일과 같게 적재돼 있습니다 — 지급일 규칙이 아직 미결입니다」~~
    #     **걷었다** (2026-09-17). 지급일이 매입일과 같은 것은 경고할 일이 아니라 **확정값
    #     N5=0 의 결과**다 (재무 확정 2026-09-10). 「미결」이 거짓이었고, 줄이 있으면 무조건
    #     붙어 원장 값과 상관없이 같은 말을 했다. 지급일은 표의 「지급」 칸이 그대로 보인다.

    return PurchaseTab(
        stats=[
            Stat(label="오늘 제안", value=str(len(plans)), unit="안",
                 detail=f"{as_of.isoformat()} · 실행 {len(runs)}건 중 고른 {len(chosen)}건",
                 raw=len(plans)),
            Stat(label="승인 대기", value=str(pending), unit="건",
                 #  🔴 «사람이» 라고 쓰지 않는다 — 걷기 구간은 AUTO-BACKFILL 로 승인된다
                 #     (`schema.PurchaseTab.committed` 주석 · 마스터 통보 2026-09-10).
                 #     빈 표 문구(`_EMPTY_COMMITTED`)와 같은 말로 맞춘다.
                 detail="안이 승인되면 확정 매입이 생깁니다",
                 tone="warn" if pending else "neutral", raw=pending),
            Stat(label="이번 주 확정 매입액", value=f"{week_amount:,}", unit="원",
                 detail=f"{week_start.isoformat()} ~ {as_of.isoformat()} · 승인분 줄 금액 합계",
                 tone="warn" if week_amount else "neutral", raw=week_amount),
            #  🔴 **규칙 3 — 창을 좁힌 날 이 칸은 `0` 이 아니라 미결이다.**
            #     이 수는 도착일에서 나온다(`_committed` 의 `arrive`). 창을 좁히면
            #     도착일이 통째로 비어 합계가 0 이 되는데, 그건 «확정된 0» 이 아니라
            #     «안 읽었다» 다. `raw=None` 이 그 구분을 나른다.
            Stat(label="확정 입고 예정", value=f"{sum(inbound):,.0f}", unit="kg",
                 detail=f"{as_of.isoformat()} 이후 도착 예정 {len(inbound)}건",
                 tone="good" if inbound else "neutral", raw=sum(inbound))
            if arrivals_complete else
            Stat(label="확정 입고 예정", value="—", unit=None,
                 detail=f"도착일을 안 읽었습니다 — 이 호출이 날짜 창을 좁혔습니다"
                        f" (window_days={window_days})",
                 tone="warn", raw=None),
        ],
        plans=plans,
        plans_note=plans_note,
        committed=committed,
        committed_note=Note(tone="neutral", text=committed_text),
        source=Source(
            filled=True, owner="매입",
            note=(
                "master_agent_runs · master_decisions · purchases · purchase_items · items"
                + (
                    ""
                    if sim_run_id is None
                    else f" · 보고 있는 실행: {sim_run_id} · 기준일: {as_of.isoformat()}"
                )
            ),
        ),
    )
