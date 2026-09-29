"""Lot 등급 정규화 — DB raw 등급을 매입 등급 어휘로 옮긴다. 근거 없는 치환은 하지 않는다.

★ 2026-09-30 재구성 BL-015: `logistics/repository.py` 의 `_normalize_grade` 와 어휘 두 벌을 옮겼다.
  현재 스냅샷
  (`domain/snapshot.py`) · 과거 조회(`domain/historical.py`) · 회전(`domain/turnover.py`)이 같은
  함수를 부른다 — 회전 계산이 함수 안에서 `repository` 를 import 하던 순환이 없어졌다.
"""



#: Purchase 등급 어휘. 원천이 이미 이 어휘면 변환이 아니므로 그대로 통과시킨다.
_PURCHASE_GRADE_VOCABULARY = frozenset({"특", "상", "중", "하"})
#: 근거가 확정된 raw → 정규화 매핑만 등록한다. 현재 확정된 매핑은 없다 —
#: 특히 `상품 → 상` 같은 임의 치환은 금지다 (등급 표준화 근거 확정 시 여기에 반영).
_RAW_GRADE_NORMALIZATION: dict[str, str] = {}


def normalize_grade(raw_grade: object) -> str | None:
    """DB raw grade를 Purchase용 정규화 등급으로 옮긴다. 근거 없으면 None."""
    if not isinstance(raw_grade, str):
        return None
    if raw_grade in _PURCHASE_GRADE_VOCABULARY:
        return raw_grade
    return _RAW_GRADE_NORMALIZATION.get(raw_grade)
