"""plan_state.py — 매입안이 **실제로 어느 상태인가**. 화면 둘이 같이 쓴다.

╔══════════════════════════════════════════════════════════════════════════╗
║  🔴 **규칙의 주인이 하나다.**                                              ║
║                                                                          ║
║  이 판정은 대시보드(`api/dashboard/presenter.py`)가 먼저 세웠고, 이제      ║
║  매입 탭(`api/purchase/presenter.py`)도 같은 낱말을 싣는다. 두 벌로 짜면   ║
║  한쪽만 고치는 날이 오고, 그날 같은 안이 화면마다 다른 상태로 뜬다.        ║
╚══════════════════════════════════════════════════════════════════════════╝

★ **여기서 DB 를 읽지 않는다.** 승인됐는지와 실매입이 적혔는지 두 사실을 받아 낱말로
  가리기만 한다. 실매입 기록을 읽는 자리는 `master/readmodel/purchase_record.py` 다.

🟢 **자리 (2026-09-29 · 재구성 BL-012).** 전에는 `app/api/plan_state.py` 였다. 가르는 사실
   (결정 · 실매입 기록)이 마스터 표에 있으므로 마스터 domain 으로 옮겼다. 매입안 이름
   「품목 · 안 이름」을 쪼개 읽는 함수(`plan_item` · `plan_label` · `recorded_for`)는 그
   이름을 짓는 매입 화면(`app/api/purchase/presenter.py`)에 남겼다 — 화면이 만든 글자 모양을
   읽는 일이라 판정이 아니다.
"""

from __future__ import annotations

__all__ = ["APPROVED", "CANDIDATE", "PLAN_STATES", "RECORDED", "REJECTED", "state_of"]

#: 매입안이 **실제로 어느 상태인가**. 화면이 쓰는 낱말은 이 넷뿐이다 (2026-09-16).
#:
#: .. code-block:: text
#:
#:     결정 없음             후보
#:     승인 · 기록 없음       승인됨
#:     승인 · 실매입 기록됨    매입 기록됨
#:     반려                  반려
#:
#: 🔴 **상태 코드를 화면에 쓰지 않는다.** `APPROVED` · `AWAITING_PURCHASE_RECORD` 같은
#:    것은 API 안쪽 어휘다 (`master/schemas/` 의 결정 · 전이 어휘). 사람이 읽는 자리에는
#:    사람 말만 쓴다.
#: 🔴 **낱말을 늘리지 않는다.** 늘리는 순간 같은 사실을 화면마다 다른 이름으로 부른다.
CANDIDATE = "후보"
APPROVED = "승인됨"
RECORDED = "매입 기록됨"
REJECTED = "반려"
PLAN_STATES = (CANDIDATE, APPROVED, RECORDED, REJECTED)


def state_of(*, approved: bool, recorded: bool) -> str:
    """안이 **실제로 어느 상태인가** 를 사람 말로 가린다.

    :param approved: 이 안이 승인됐나 (안 단위 — 아래).
    :param recorded: 이 안에 실매입 기록이 적혔나. 기록을 못 읽었거나 없으면 거짓이다.

    🟢 2026-09-29 (재구성 BL-012) 전에는 `recorded` 로 기록 합계(`RecordedTotals`)나
       `None` 을 받았다. 판정이 본 것은 `None` 인가 하나뿐이라, 마스터 domain 으로 옮기며
       그 사실만 받게 했다. 부르는 쪽이 `기록 is not None` 을 넘긴다.

    🔴 종전에는 `승인 대기` 아니면 **`후보`** 였다. `pending` 이 거짓이라는 것은 «결정이
       났다» 는 뜻인데 화면에는 「후보」가 찍혔다 — 사람이 읽으면 **사실과 정반대**다.

    .. code-block:: text

        실측  dev@1df31f8 · SIM-CHECK-HOLIDAY-0916 · 2026-04-13
          배추 574 × 491  281,834   "후보"   🔴 승인 + 실매입 기록 완료
          무   403 × 196   78,988   "후보"   🔴 승인 + 실매입 기록 완료
        같은 응답의 「이번 주 확정 매입액」 353,988 은 그 둘의 **기록값**이었다

    ★ **`pending` 이 아니라 `approved` 로 가른다.** 🔴 둘은 **서로 반대가 아니다**
      (2026-09-17 · `#813`) — `pending` 은 **요청** 단위(같은 요청에 결정이 없나)이고
      `approved` 는 **안** 단위(이 안이 승인됐나)다. 같은 요청에서 다른 안이 승인되면
      고르지 않은 형제 안은 **둘 다 거짓**이다. 이 칸이 말하려는 것은 **이 안의 승인 여부**다.

    .. code-block:: text

        결정 없는 요청의 안            pending 참     approved 거짓   → 후보
        승인된 안                      pending 거짓   approved 참     → 승인됨 · 매입 기록됨
        같은 요청에서 고르지 않은 안    pending 거짓   approved 거짓   → 후보

        실측  REH-0914 08-31 · 배추 · 기본 (보수가 승인된 요청)   pending 거짓 · approved 거짓

    ⚠️ **「반려」를 지금은 아무도 안 낸다.** 거절(`REJECT_ALL`)은 `scenario_label` 이 NULL
      이라(`master_decisions` CHECK) 안 하나에 붙지 않고, 매입 탭이 주는 `Plan` 에는 그
      사실을 실을 칸이 없다. 지어내지 않고 「후보」로 둔다 — 낱말만 어휘에 세워 둔다.
    """
    if not approved:
        return CANDIDATE
    return RECORDED if recorded else APPROVED
