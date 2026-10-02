"""도착 처리의 실패 종류.

막힘 사유 어휘 · 검수 사실 · 검수 원천(Protocol)은 도착 판정 타입(`domain/arrival.py`)을
쓰므로 `domain/inbound_execution.py` 에 있다. 순서는 `service/inbound_execution.py`, 등록소
표면은 `adapter.LogisticsInboundExecution`.
"""

from __future__ import annotations


class InboundExecutionError(RuntimeError):
    """이 모듈이 내는 실패의 조상 (`inbound_stock.InboundStockError` 와 같은 결)."""


class UnknownReceiptStage(InboundExecutionError, ValueError):
    """Receipt 상태를 어느 단계로도 못 읽는다.

    아는 단계로 접어 읽지 않는다. 어휘가 늘었는데 이 파일이 안 따라온 상태라,
    검수 전으로 보면 이미 적힌 검수를 덮으려 들고 검수 후로 보면 검수를 건너뛴 채
    재고를 만든다. 둘 다 에러 없이 틀리는 쪽이다.

    `check_receipt_state` 가 DB CHECK 어휘 밖 값을 이미 막으므로, 여기 오는 것은
    어휘가 늘었다는 뜻이다.
    """
