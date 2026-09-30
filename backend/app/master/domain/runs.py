"""실행 이력 값 규칙 — 빈 축을 NULL 로, 걷기 범위 검사.

★ 2026-09-30 재구성 BL-018: `master/run_repository.py` 에서 옮겼다 — `null_if_blank`,
  `check_walk_scope`.
"""

from __future__ import annotations

from datetime import date


def null_if_blank(value: str | None) -> str | None:
    """빈 문자열을 `None` 으로 접는다. **이 모듈이 그 주인이다.**

    🔴 **왜 필요한가.** `ExecutionContext.sim_run_id` 의 기본값이 `""` 이고 그것은
      *"아직 안 실렸다"* 는 뜻이다 (`envelope.py` 의 ①②③). 그대로 넣으면 표 안에
      **모르는 것이 두 모양**으로 앉는다 — 옛 행은 NULL, 안 실린 새 행은 `''`.
      그러면 *"축이 없는 실행"* 을 세는 질문에 `IS NULL` 만으로는 답이 안 나오고,
      `sim_run_id = ''` 를 잊은 조회가 조용히 틀린 수를 낸다.

    ★ **접는 자리를 여기 하나로 둔다.** `record_*` 넷이 저마다 접으면 하나를
      빠뜨렸을 때 그 사이클만 `''` 를 적고, 아무 오류도 안 난다.
    """
    if value is None:
        return None
    return value or None


def check_walk_scope(*, sim_run_id: str, start: date, end: date) -> str:
    """성적표가 설 수 있는 물음인지 보고, **정규화한 축**을 돌려준다.

    ★ **집계와 성적표가 같은 문장을 쓴다.** 둘 다 이 규칙이 필요하고(하나는 WHERE
      절을 만들고 하나는 날을 만든다), 두 벌로 적으면 한쪽만 고치는 날 진입점이
      400 을 안 내면서 빈 성적표를 내보낸다.

    :raises ValueError: 축이 비었거나 범위가 뒤집혔을 때.
    """
    axis = null_if_blank(sim_run_id)
    if axis is None:
        raise ValueError(
            "sim_run_id 없이 성적표를 셀 수 없다 — 어느 걷기인지 없으면 물음이 성립하지 않는다"
        )
    if end < start:
        raise ValueError(f"범위가 뒤집혔다: {start.isoformat()} ~ {end.isoformat()}")
    return axis
