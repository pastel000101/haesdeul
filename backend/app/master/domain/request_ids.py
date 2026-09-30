"""업무 키(request_id) 규칙 — 실행 축 · 날짜로 매입 · 판매 · 장부 관문 키를 짓고 알아본다.

★ 2026-09-30 재구성 BL-018: `master/service.py` 에서 옮겼다 — `make_request_id`.
★ 2026-09-30 재구성 BL-018: `master/run_repository.py` 에서 옮겼다 — `LEDGER_GAP_END_CODE`,
  `DAILY_REQUEST_HEAD`, `_LEDGER_GAP_REQUEST_SUFFIX`, `_LEDGER_GAP_REQUEST_TAIL`,
  `LEDGER_GAP_REQUEST_LIKE`, `build_request_id`, `ledger_gap_request_id`,
  `is_ledger_gap_request_id`.
★ 2026-09-30 재구성 BL-018: `master/scheduler.py` 에서 옮겼다 — `_SALES_REQUEST_HEAD`,
  `daily_request_id`, `daily_sales_request_id`.
"""

from __future__ import annotations

from datetime import date

#: 장부 관문이 막아 **판단을 한 번도 안 돌린 날**이 다는 종료 코드 (`#465`).
#:
#: ★ **주인이 여기다.** `persistence.record_ledger_gap` 이 이 값을 적는다.
#:
#: 🔴 **이 값으로 관문 행을 되찾지 않는다** (2026-09-09 에 판정에서 뺐다). *"시작
#:   못 했다"* 는 관문 행만의 사실이 아니다 — 실측으로 `PROCUREMENT · E4_NOT_STARTED
#:   · item IS NULL` 이 14행이고 전부 품목을 정하기 전에 죽은 옛 매입 실행이다
#:   (*"경계를 내지 못한 에이전트: finance"*). 그 14행이 오늘 안 새는 이유는 `sim_run_id`
#:   가 전부 NULL 이라 축이 막고 있기 때문이지, 모양이 스스로를 증명해서가 아니다.
#:   축이 실린 채로 품목 전에 죽는 실행이 한 번만 나오면 그날이 *"관문이 막았다"* 로
#:   잘못 읽힌다. **되찾는 것은 아래 업무 키다.**
LEDGER_GAP_END_CODE = "E4_NOT_STARTED"

#: 하루 단위 업무 키의 **머리**. `scheduler` 의 매입·판매 키와 관문 키가 같이 쓴다.
#:
#: ★ **주인이 여기다** — 꼬리 상수(`_LEDGER_GAP_REQUEST_SUFFIX`)와 `LIKE` 패턴이
#:   이 파일에 있으니 머리도 같이 둔다. 머리와 꼬리가 다른 파일에 흩어지면 축을
#:   어디에 끼우는지가 두 벌이 된다.
DAILY_REQUEST_HEAD = "REQ-DAILY"

#: 장부 관문 행의 업무 키 꼬리. **업무 키의 품목 자리에 들어간다.**
#:
#: 🔴 **품목 이름과 겹치면 안 된다.** 겹치는 순간 그날 그 품목의 판단 행과 게이트
#:   행이 같은 업무 키를 갖고, `get_run_by_request_id` 가 둘을 못 가른다.
#:   계약 품목은 한글 이름이라 이 꼬리와 같아질 수 없고, 그것을 검사가 잠근다.
#:
#: ★ **주인이 `scheduler` 가 아니라 여기다** (2026-09-09 에 옮겼다). 되찾는 쪽
#:   (`count_runs_by_day` · `procurement_boundary`)이 이 값을 봐야 하는데,
#:   `run_repository → scheduler` 는 `scheduler → persistence → run_repository`
#:   와 고리를 만든다. *"행의 정체는 저장소가 소유한다"* 가 맞다 — `scheduler` 는
#:   여기서 가져다 쓰고 이름만 다시 내보낸다.
_LEDGER_GAP_REQUEST_SUFFIX = "LEDGER-GAP"

#: 업무 키가 그 꼬리로 끝나는가를 보는 문자열. **한 상수에서 나온다.**
_LEDGER_GAP_REQUEST_TAIL = f"-{_LEDGER_GAP_REQUEST_SUFFIX}"

#: SQL 이 같은 꼬리를 찾을 때 쓰는 `LIKE` 패턴.
#:
#: 🔴 **날짜 형식(`REQ-DAILY-YYYYMMDD-`)을 SQL 에 다시 적지 않는다.** 두 벌이 되면
#:   `ledger_gap_request_id` 만 바뀌는 날 성적표가 조용히 갈린다. 꼬리 하나만
#:   맞춘다 — 그 꼬리는 `is_ledger_gap_request_id` 가 보는 것과 같은 값이다.
LEDGER_GAP_REQUEST_LIKE = f"%{_LEDGER_GAP_REQUEST_TAIL}"


def build_request_id(*, head: str, as_of: date, sim_run_id: str, tail: str) -> str:
    """업무 키 하나를 짓는다 — `{head}-{sim_run_id}-{YYYYMMDD}-{tail}` (2026-09-11).

    ★★ **자리 배치의 주인이 하나다.** 업무 키를 짓는 자리가 셋이고
      (`scheduler.daily_request_id` · `scheduler.daily_sales_request_id` ·
      `ledger_gap_request_id`), 축이 어느 자리에 붙느냐는 **셋이 같아야 하는 사실**
      이다. 세 곳이 각자 f-string 을 쓰면 한 곳만 축을 뒤로 옮기는 날
      `LEDGER_GAP_REQUEST_LIKE` 가 조용히 그 행을 못 찾는다.

    🔴 **축이 꼬리 앞에 붙는다. 꼬리 뒤가 아니다.**

      되찾는 쪽이 실측으로 **꼬리**를 본다 — `LEDGER_GAP_REQUEST_LIKE` 가
      `'%-LEDGER-GAP'` 이고 `is_ledger_gap_request_id` 가 `endswith` 다. 축을 뒤에
      붙이면 그 둘이 한 행도 못 집고, 성적표의 `gate_blocked` 와
      `procurement_boundary` 의 `LEDGER_GAP` 이 **에러 없이 늘 거짓**이 된다.
      머리에 붙이면 꼬리가 그대로라 두 조회가 손대지 않고 산다.

      ⚠️ `request_id` 를 **앞머리로 찾는 SQL 은 없다** (2026-09-11 전수 실측).
        `transition.purchase_id_prefix_for` 가 `PUR-{request_id}-D{seq}-S` 로 앞머리를
        만들지만 양쪽 다 같은 `request_id` 에서 나오므로 자리와 무관하다.

    🔴 **축을 지어내지 않는다.** 빈 축은 `REQ-DAILY--20260105-무` 가 되고, 그 모양은
      축을 안 실은 모든 호출자에게서 **같은 문자열**이라 실행이 달라도 키가 겹친다 —
      이 판이 없애려는 바로 그 자리다. 필수 인자가 빠뜨림을 막고 이 검사가 빈 값을
      막는다.

    ★ **시각을 안 넣는다.** 축은 시각이 아니다 — 같은 실행이 같은 날 두 번 깨어나면
      **키가 같아야** 사람이 그 둘을 같은 업무로 읽는다.

    🔴 **그 키가 두 번째 행을 막지는 않는다** (2026-09-11 · 매입 지적 · 실측).

      ```text
      master_agent_runs_pkey                 UNIQUE (run_id)          ← run_id 가 PK 다
      master_agent_runs_run_request_unique   UNIQUE (run_id, request_id)
      ```

      ★★ **PK 를 품은 복합 유니크는 아무것도 더 막지 않는다.** `run_id` 가 이미
        유일하므로 그 인덱스는 **언제나 통과**한다. 종전 주석이 이것을 *"두 번째를
        막는다"* 로 적었는데 **거짓이었다** — 적어 놓고 한 번도 안 쟀다.

      🟢 **그래도 인덱스를 안 바꾼다.** 같은 업무 키에 행이 여럿 서는 것은 **의도**다 —
        판단은 걸을 때마다 다시 서고, 그래야 부서가 고친 것을 다시 잴 수 있다.
        실측: `SIM-WALK-2026-V4` 는 창을 두 번 걸어 행 854 · 업무 키 428 이다.

      ⚠️ **그래서 세는 축을 밝혀야 한다.** 재실행이 있는 실행에서 **행으로 세면
        부풀려진다.** `(as_of, item, cycle)` 로 세고 몇 번 걸었는지를 같이 적는다.

    :raises ValueError: `sim_run_id` 가 비었을 때.
    """
    axis = sim_run_id.strip() if sim_run_id else ""
    if not axis:
        raise ValueError(
            "sim_run_id 없이 업무 키를 지을 수 없다"
            " — 축이 없으면 다른 실행의 결정이 이 실행의 것으로 읽힌다"
        )
    return f"{head}-{axis}-{as_of:%Y%m%d}-{tail}"


def ledger_gap_request_id(as_of: date, *, sim_run_id: str) -> str:
    """`REQ-DAILY-SIM-WALK-202601-20260908-LEDGER-GAP`. **하루 단위 키다 — 품목이 없다.**

    🔴 **`scheduler.daily_request_id` 를 못 쓴다.** 저쪽은 품목별인데 장부 관문은
      하루를 통째로 돌려세운다. 품목을 하나 골라 넣으면 *"배추 때문에 막혔다"* 라는
      없는 사실이 생기고, 전부에 넣으면 같은 사실이 품목 수만큼 쌓인다.

    🔴 **실행 축이 필수다** (2026-09-11). 축이 없으면 어제 걷던 실행이 남긴 관문 행의
      키를 오늘 새 실행이 그대로 짓고, *"그날 게이트 행이 이미 있나"* 가 **남의
      실행 행**에 참이 된다.

    🔴 **시각을 안 넣는다** (`daily_request_id` 와 같은 이유). 넣으면 같은 날 두 번
      깨어날 때 키가 갈리고, 그러면 *"그날 게이트 행이 이미 있나"* 를 물을 수가 없다.

    ★ **적는 쪽과 되찾는 쪽이 이 함수 하나를 본다.** `persistence.record_ledger_gap`
      이 이 값을 `request_id` 로 적고, `is_ledger_gap_request_id` 가 같은 꼬리로
      그 행을 알아본다.

    ★ **문자열을 여기서 다시 잇지 않는다** — 자리 배치의 주인은 `build_request_id` 다.
    """
    return build_request_id(
        head=DAILY_REQUEST_HEAD,
        as_of=as_of,
        sim_run_id=sim_run_id,
        tail=_LEDGER_GAP_REQUEST_SUFFIX,
    )


def is_ledger_gap_request_id(request_id: str | None) -> bool:
    """그 업무 키가 **장부 관문 행의 것인가.**

    ★ 날짜를 안 본다 — 어느 날 것인지는 `as_of` 칸이 이미 말한다. 여기서 날짜까지
      맞추면 형식이 두 벌이 되고, 그것이 이 판이 없앤 자리다.
    """
    return bool(request_id) and request_id.endswith(_LEDGER_GAP_REQUEST_TAIL)  # type: ignore[union-attr]


#: 판매 키의 머리. **매입 머리에서 갈라 나온다 — 문자열을 다시 적지 않는다.**
_SALES_REQUEST_HEAD = f"{DAILY_REQUEST_HEAD}-SALES"


def daily_request_id(as_of: date, item: str, *, sim_run_id: str) -> str:
    """`REQ-DAILY-SIM-WALK-202601-20260908-배추`. **실행·날짜·품목으로 정해진다.**

    🔴🔴 **실행 축이 필수다** (2026-09-11 · `#`). 없으면 **새 실행이 옛 실행의 승인을
      물려받는다.**

      ```text
      실측  업무 키 `REQ-DAILY-20260105-무`  행 16건 · 실행 5개에 걸쳐 있음
            그 업무 키의 master_decisions 행    1건
      ```

      `decision_repository.list_decisions` 는 `WHERE request_id = %s` 뿐이라 축을 안
      본다. 그래서 A 실행에서 난 승인이 B 실행에서 `ALREADY_DECIDED` 로 읽히고,
      **B 는 자기 원장을 영영 못 만든다** — 새 실행을 열고 2주를 걸었더니
      `ALREADY_DECIDED 24 · RECORDED 0 · 전이 {}` 였다.

    🔴 **기본값을 두지 않는다.** 기본값이 있으면 축을 빠뜨린 호출이 조용히 옛 모양으로
      떨어지고, 그 실패는 `ALREADY_DECIDED` 로만 나타나 **에러가 안 난다.** 문법이
      막게 한다.

    🔴 **시각을 넣지 않는다. 축은 시각이 아니다.** 넣으면 같은 날 두 번 깨어날 때 id 가
      갈리고, **같은 업무가 두 업무로 읽힌다.** 축은 같은 실행 안에서 안 변하므로 그
      읽기를 안 건드린다.

      ⚠️ **인덱스가 그 둘째 행을 막아 주지는 않는다** — 이유는
        `run_repository.build_request_id` 가 적는다 (`run_id` 가 PK 라 그 복합
        유니크는 언제나 통과한다). 종전 이 자리의 *"멱등이 인덱스에 걸린다"* 는
        **거짓이었다.**

    ★ **자리 배치는 `run_repository.build_request_id` 가 정한다** — 축이 꼬리 앞에
      붙는 이유가 거기 적혀 있다 (`LEDGER_GAP_REQUEST_LIKE` 가 꼬리를 문다).
    """
    return build_request_id(
        head=DAILY_REQUEST_HEAD, as_of=as_of, sim_run_id=sim_run_id, tail=item
    )


def daily_sales_request_id(as_of: date, item: str, *, sim_run_id: str) -> str:
    """`REQ-DAILY-SALES-SIM-WALK-202601-20260908-배추`.

    🔴 **매입 키와 갈라야 한다** (2026-09-10).

      **판매가 `daily_request_id` 를 그대로 쓰면 안 된다.** 같은 날 같은 품목이면
      문자열이 같아지고, 그러면 `get_run_by_request_id` 가 **어느 사이클의 실행인지
      못 가른다.**

      ⚠️ 종전 이 자리는 *"인덱스가 두 번째 사이클을 막는다"* 고 적었는데 **거짓이었다**
        (`run_repository.build_request_id` 참고). 막는 것이 아니라 **두 사이클이
        한 업무 키에 뒤섞이는 것**이 문제다 — 막혀서 안 남는 것이 아니라 남는데
        구별이 안 된다.

    🔴 **실행 축이 필수다** (2026-09-11). 이유는 `daily_request_id` 가 적어 둔 그대로다 —
      축이 없으면 판매 승인도 남의 실행 것을 물려받는다.

    🔴 **시각을 넣지 않는다.** 이유는 `daily_request_id` 가 적어 둔 그대로다 —
      넣으면 같은 날 두 번 깨어날 때 id 가 갈리고, 멱등이 인덱스가 아니라
      *"두 번 안 깨우기"* 에 걸리게 된다.
    """
    return build_request_id(
        head=_SALES_REQUEST_HEAD, as_of=as_of, sim_run_id=sim_run_id, tail=item
    )


def make_request_id(as_of: str, seq: int = 1) -> str:
    """`REQ-20260826-0001`.

    ★ 시각이 아니라 **날짜 + 순번**이다. 같은 날 재실행을 구분하되 재현 가능해야 한다
      (§1.2-11). 순번 관리는 호출자 몫이며, 명시적으로 주는 편이 낫다.
    """
    return f"REQ-{as_of.replace('-', '')}-{seq:04d}"
