"""재무 파트 구현 — 마스터 등록소 Protocol 을 재무 함수에 잇는 **표면** 셋.

```text
등록소 (registry/)  Protocol 메서드                  표면 (여기)                  본문
cancellation        cancel(conn, *, commitment, …)   FinanceCancellationAdapter   (이 파일)
collection          collect(conn, *, as_of)          FinanceCollectionAdapter     service/collection
receivable          issue(conn, *, as_of)            FinanceReceivableAdapter     service/receivable
```

본문 함수: `service/collection.collect_finance_receipts` ·
`service/receivable.issue_finance_receivables`.

★ 2026-09-30 재구성 BL-018: `master/finance_cancellation.py` · `finance_collection.py` ·
  `finance_receivable.py` 를 이 파일로 모았다(쟁점 11 배치안). Protocol · 생성 인자 · 대역 자리
  (`read_axis` · `load_events` · `load_sales` · `confirm`) · 등록 줄(`registry/bootstrap.py`)은
  그대로다.
  수금 · 채권의 업무 본문(축 조회 · 불일치 BLOCKED · 읽기 · 건별 호출 · 집계)은 service 로 옮겼고,
  옛 두 파일의 머리말도 그 service 파일로 함께 갔다. 받은 연결은 그대로 넘긴다 — 여기서 빌리거나
  commit 하지 않는다.

  ⚠️ 재무 쪽 `app/finance/adapter.py` 에도 같은 이름의 `FinanceCancellationAdapter` 가 있다(재구성
    전부터 · 서명이 다르다 — `purchase_ids: Sequence[str]` · `as_of` · 결과를 돌려준다). 등록되는
    것은
    **이 파일의 것**이고, 재무 쪽은 재무 검사만 쓴다. 이름이 같다고 합치지 않았고, 앱 안 호출이
    없다는 이유만으로 지우지 않았다 — 처리 방침은 설계 쟁점 4(죽은 코드 · 미배선 제거 범위) 결정
    대기.

─── 취소 표면 (옛 `master/finance_cancellation.py` 머리말 · 그대로) ───────────────────────

  finance_cancellation.py — 마스터 `ApprovalCancellation` 을 재무 취소 함수에 잇는 배선.

  🔴 **왜 `app/finance/` 가 아니라 여기인가.**

    처음에 `app/finance/cancellation_adapter.py` 로 썼더니 재무의 경계 검사 둘이
    울었다 —

    ```text
    test_finance_touches_master_only_through_shared_contract_modules
      재무가 마스터에서 아는 것은 app.master.envelope · app.master.critic_bridge 뿐이다
      이 어댑터가 app.master.commitment 를 임포트한다        🔴
    ```

    ★ **재무가 옳다.** 어댑터는 *"마스터가 재무를 어떻게 부르는가"* 이지 재무 지식이
      아니다. **배선은 조율자 몫**이고, 재무 모듈이 마스터를 알기 시작하면 화살표가
      양방향이 된다.

    ⚠️ 물류 쪽(`app/logistics/service/cancellation.py`)은 다른 자리에 둔 이유가 있다 — 그쪽은
      *"어떻게 걷는가"* 라는 **물류 도메인 규칙**을 담는다(마스터가 대신 썼을 뿐이다).
      여기는 **이름 하나를 옮기는 것**이 전부다.

  ★ **재무가 그렇게 하겠다고 적었다** (회신 2026-09-06 `§5`) —
    *"취소 관통 구현 시 Adapter 경계만 새 Protocol 에 맞추겠습니다."*

  🔴 **이 파일이 하는 일은 이름 하나를 옮기는 것뿐이다.**

  ```text
  마스터 Protocol   cancel(conn, *, commitment, cancelled_on, target_state_date, purchase_ids,
                           financing_mode)
  재무 함수         cancel_finance_payables(conn, *, purchase_ids, as_of, target_state_date,
                                            financing_mode)

  cancelled_on  →  as_of        재무 내부의 as_of 는 **취소 사건일** 의미다 (회신 §1)
  purchase_ids  →  values()     재무는 Sequence[str] 를 받는다 (seq 는 안 본다)
  ```

  ⚠️ **`commitment.as_of` 를 넘기지 않는다.** 그것은 **원 승인일**이고, 재무가 그 값을
    취소일로 받으면 **과거 상태를 고치게 된다** — 그 날에는 실제로 미지급이 있었다.
    재무가 직접 짚어 준 자리다 (회신 `§4`).

  ⚠️ **`target_state_date - 1` 로 역산하지 않는다.** 지금은 그 뺄셈이 맞지만 규칙이
    바뀌는 날 취소일이 조용히 따라 틀린다 — 같은 사실을 두 곳에서 만들지 않는다.

  ★ **결과를 버린다.** `FinanceCancellationResult` 는 *"이번에 실제로 물린 금액"* 을
    말하는데, 마스터 `CancellationOut` 은 지금 그 칸이 없다. **버리는 것을 적어 둔다** —
    나중에 화면이 *"3,063,298원이 물렸습니다"* 를 쓰려면 여기서 위로 올려야 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.contracts.commitment import ApprovedCommitment
from app.contracts.parts import CollectionPartOut, ReceivablePartOut
from app.finance.readmodel.finance_state import get_finance_runtime_axis
from app.finance.schemas.collections import CollectionEvent
from app.finance.schemas.finance_state import FinanceRuntimeAxis
from app.finance.service.cancellation import cancel_finance_payables
from app.finance.service.receivables import confirm_receivable
from app.master.readmodel.collection_events import read_collection_events
from app.master.repository.sales_reads import ConfirmedSale, read_confirmed_sales
from app.master.service.collection import collect_finance_receipts
from app.master.service.receivable import issue_finance_receivables


class FinanceCancellationAdapter:
    """`ApprovalCancellation` 구현. **생성 인자가 없다.**

    ★ 재무 취소는 `purchase_id` 로 행을 찾으므로 실행 정체성이 필요 없다 —
      `LogisticsCancellationAdapter` 가 `sim_run_id` 를 받는 것과 다른 점이다.
    """

    def cancel(
        self,
        conn: Any,
        *,
        commitment: ApprovedCommitment,
        cancelled_on: date,
        target_state_date: date,
        purchase_ids: Mapping[int, str],
        financing_mode: str,
    ) -> None:
        """Master가 고른 실행 축을 바꾸지 않고 Finance에 전달한다."""
        ids = [purchase_ids[seq] for seq in sorted(purchase_ids)]
        if not ids:
            # ★ 회차 일정이 없던 약정도 승인은 살아 있다 — 물릴 채무가 **없다**는 것은
            #   정상 상태다. 빈 목록을 재무에 넘기면 재무가 *"요청 집합이 비었다"* 로
            #   판단할 자리를 만들게 되므로 여기서 멈춘다.
            return
        cancel_finance_payables(
            conn,
            purchase_ids=ids,
            # 🔴 **취소 사건일이다.** commitment.as_of(승인일)가 아니다.
            as_of=cancelled_on,
            target_state_date=target_state_date,
            financing_mode=financing_mode,
        )


#: 사건을 읽는 방법의 모양. 축 둘을 받아 그 축의 사건 전부를 준다.
LoadEvents = Callable[..., tuple[CollectionEvent, ...]]


@dataclass
class FinanceCollectionAdapter:
    """`CollectionSource` 구현. **재무 축을 물어보고 그 축의 사건을 실어 넘긴다.**

    :param sim_run_id: 마스터가 정한 실행. `BURN_IN_SIM_RUN_ID` 하나가 주인이다.
    :param read_axis: 재무 축을 읽는 방법. 기본값이 재무 함수 그대로이고, 검사가
        대역을 끼울 자리다. **마스터가 축을 계산하는 자리가 아니다.**
    :param load_events: 수금 사건을 읽는 방법. 기본값이 `master_collection_events`
        조회이고, 검사가 대역을 끼울 자리다. **호출마다 다시 읽는다** — 배선 시점에
        고정하면 표에 한 줄 넣어도 앱을 다시 띄우기 전까지 아무 일도 안 일어난다.
    """

    sim_run_id: str
    read_axis: Callable[..., FinanceRuntimeAxis] = field(default=get_finance_runtime_axis)
    load_events: LoadEvents = field(default=read_collection_events)

    def collect(self, conn: Any, *, as_of: date) -> CollectionPartOut:
        """재무 축을 읽어 `FinanceCollectionSource` 를 세우고 위임한다."""
        return collect_finance_receipts(
            conn,
            as_of=as_of,
            sim_run_id=self.sim_run_id,
            read_axis=self.read_axis,
            load_events=self.load_events,
        )


#: 판매를 읽는 방법의 모양. 커넥션과 날짜를 받아 그날 확정분을 준다.
LoadSales = Callable[..., tuple[ConfirmedSale, ...]]


@dataclass
class FinanceReceivableAdapter:
    """`ReceivableSource` 구현. **재무 축을 물어보고 그날 확정분을 재무에 넘긴다.**

    :param sim_run_id: 마스터가 정한 실행. `BURN_IN_SIM_RUN_ID` 하나가 주인이다.
    :param read_axis: 재무 축을 읽는 방법. 기본값이 재무 함수 그대로이고, 검사가
        대역을 끼울 자리다. **마스터가 축을 계산하는 자리가 아니다.**
    :param load_sales: 그날 확정 판매를 읽는 방법. **호출마다 다시 읽는다** — 배선
        시점에 고정하면 판매가 한 줄 들어와도 앱을 다시 띄우기 전까지 아무 일도 안
        일어난다.
    :param confirm: 채권을 세우는 재무 경계. 기본값이 `confirm_receivable` 자체다 —
        **마스터가 원장에 직접 쓰지 않는다.**
    """

    sim_run_id: str
    read_axis: Callable[..., FinanceRuntimeAxis] = field(default=get_finance_runtime_axis)
    load_sales: LoadSales = field(default=read_confirmed_sales)
    confirm: Callable[..., Any] = field(default=confirm_receivable)

    def issue(self, conn: Any, *, as_of: date) -> ReceivablePartOut:
        """재무 축을 읽고 그날 확정 판매를 한 건씩 `confirm_receivable` 에 넘긴다."""
        return issue_finance_receivables(
            conn,
            as_of=as_of,
            sim_run_id=self.sim_run_id,
            read_axis=self.read_axis,
            load_sales=self.load_sales,
            confirm=self.confirm,
        )
