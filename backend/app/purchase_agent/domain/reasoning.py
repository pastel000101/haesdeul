"""봉투 회신의 설명문(reasoning) — 라벨만으로 짓는 세 문장 이하 (M-1 §5.4)."""

from collections.abc import Mapping
from typing import Any

# ── 출력 ──────────────────────────────────────────────────────────────────


def build_reasoning(proposal: Mapping[str, Any]) -> str:
    """문장 3개 이하 · 3자리 이상 연속 숫자 금지 (M-1 §5.4).

    숫자를 아예 넣지 않는 쪽을 택했다. 봉투의 검사가 ``\\d[\\d,]{2,}``라 세 자리부터
    걸리는데, 안 개수·커버일수 같은 값은 한두 자리라 통과한다 — 그래도 서술문에 숫자를
    실으면 출처를 붙일 수 없다. 숫자는 payload와 Evidence가 싣는다.

    ``"2안"``·``"D+7"``은 통과하지만, 그 통과에 기대지 않고 라벨만 쓴다.
    """
    labels = [s["label"] for s in proposal.get("scenarios", [])]
    if not labels:
        # 원인을 단정하지 않는다. 0안 원인은 셋이고(#70), 규격 미확정 · 적재 정지는
        # 제약 때문이 아니다. 화면은 이 문장을 1행으로 그대로 옮기므로
        # (`ProcurementResult.tsx`), "제약 조합 하에" 같은 말을 넣으면 거기까지만 읽는
        # 사람은 창고·현금에 걸린 줄 안다. 원인은 아래 두 줄이 말한다 —
        # ``no_proposal_reason`` 과 ``rejected_reasons``.
        return "유효한 안이 없어 제안을 내지 못했다."
    head = "·".join(labels)
    # 열린 축을 실제 값에서 읽는다. stable이라도 축이 다 열리는 것은 아니다 — 배추는
    # 편중 게이팅으로 mix가 닫혀 두 축뿐이라, 고정 문구를 쓰면 서술문이 산출물과
    # 어긋난다. 봉투의 숫자·문장 검사는 이 종류의 불일치를 못 잡는다.
    axes = "·".join(proposal.get("allowed_axes") or []) or "없음"
    tail = (
        "예측 구간이 넓어 공격안은 만들지 않았다."
        if proposal.get("situation") == "uncertain"
        else "예측 구간이 안정 범위다."
    )
    return f"{head} 안을 냈다. {tail} 열린 전략축은 {axes}이다."
