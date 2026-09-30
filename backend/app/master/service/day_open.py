"""
day_open.py — **하루를 여는 진입점**. 그날 상태 행을 보장한다.

지금 그날 상태 행을 만드는 것은 **승인뿐**이다. 승인이 없는 날은 다음 날 행이 안
생기고, 그러면 두 파트가 같이 막힌다.

```text
물류   logistics_runtime_fixture 를 as_of 정확 일치로 고른다 → LookupError
재무   state_date 가 as_of 와 다르면 fail-closed
```

★ **`open_day` 가 그날 상태 행을 보장한다** — 없으면 전날에서 물려받아 만든다.

🔴 **명시적 호출이다. 실행의 부작용이 아니다.**

```text
🔴 안 함   run_procurement 이 시작할 때 자동으로 연다
🟢 함      누군가 "다음 날로 간다" 를 명시적으로 부른다 (POST /master/days/{as_of}/open)
```

  판단 한 번이 장부를 바꾸면 *"같은 as_of 로 백번 돌려도 같은 답"* 이 깨진다.
  조회하려고 돌린 실행이 상태를 만들면 안 된다. **하루가 넘어가는 것은 사건이지
  부작용이 아니다.**

★ **분담은 `transition.py` 와 같다.**

```text
파트    자기 표의 그날 행을 만든다        무엇을 물려받을지는 파트가 안다
마스터  언제 · 어느 날까지 · 한 트랜잭션   달력과 경계는 마스터 것이다
```

🔴 **이 모듈에 SQL 이 있으면 그 분담이 무너진다.** `test_day_open.py` 가 원문을 읽어
   막는다 — `transition.py` 의 같은 검사와 짝이다.

🔴 **`with conn:` 을 쓰지 않는다.** psycopg3 의 커넥션 컨텍스트 매니저는 블록이
   정상 종료하면 자동으로 commit 한다. 그러면 "커밋은 마스터가 한 번만 한다"는
   규율이 문법에 숨고, 변이 검사(커밋 지우기)도 안 걸린다. 연결은 공통 풀에서
   `with borrow() as conn:` 으로 빌리고 블록 끝에 돌려준다 — 그 반환은 commit 하지 않는다
   (2026-09-29 풀 전환 · `app/core/db.py`).

⚠️ **등록된 구현이 아직 없다.** 재무는 미회신이고 물류는 파트 소유다. 등록이 0건이어도
   이 경로가 도는 것이 정상이다 — `transition.py` 가 `#238` 에서 그렇게 만들어졌다.

★ 2026-09-30 재구성 BL-018: `master/day_open.py` 에서 옮겼다. 역할이 다른 부분은 갈랐다 —
  `registry/day_open.py`; `schemas/day_open.py`. 무엇이 어디로 갔는지는 설계서 대응표 `master/` 절.
  함께 모은 것: `master/day_opening_repository.py` 의 `logger`, `record_day_opening`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from datetime import date, timedelta
from typing import Any

from app.core import db as core_db
from app.core.settings import get_db_schema
from app.master.domain.calendar_walk import MAX_WALK_DAYS
from app.master.registry.day_open import PARTS, DayOpenPart, missing, registered
from app.master.registry.sim_run_binding import bind_sim_run
from app.master.repository.day_openings import upsert_day_opening
from app.master.schemas.collection_seed import CollectionSeedOutcome
from app.master.schemas.day_open import DayOpenOut, DayOpenPartOut
from app.master.service.collection_seed import seed_day

#: 뒤로 몇 날까지 열린 날을 찾을 것인가.
#:
#: ⚠️ **넘으면 막고 사유를 낸다 — 행을 만들지 않는다.** 실수로 먼 날을 열면 수백 행이
#:    조용히 생긴다. 번인이 30일이므로 그보다 크게 건너뛰는 것은 의도보다 실수일
#:    가능성이 크다.
#:
#: 🔴 **수를 여기 적어 두지 않는다** (`#282`). 달력을 하루씩 걷는 자리가 마스터에 둘이고
#:    (여기는 뒤로, 실행일 달력은 앞으로) 멈추는 이유가 같다. 같은 수를 두 곳에 적으면
#:    언젠가 한쪽만 바뀐다 — 이유와 수는 `calendar_walk.py` 에 한 번만 있다.
#:
#: ★ **이름은 남긴다.** 하루 넘김의 어휘는 *"물려받는다(carry)"* 이지 *"걷는다"* 가
#:   아니고, 밖에서 이 이름을 부르고 있다 (`tests/master/test_day_open.py`).
MAX_CARRY_DAYS = MAX_WALK_DAYS

#: 🔴 **강제 개장이 푸는 상한.** 평소 상한(`MAX_CARRY_DAYS`)만 풀고 그 이상은 안 연다.
#:
#: ★ **강제 개장이 무한대가 아니다** (계약 §5 · `day_gate` 의 `SPLIT_THRESHOLD_DAYS`).
#:   366일을 넘기면 관리자가 눌러도 안 열리고, 그때는 나눠서 불러야 한다
#:   (`SPLIT_FORCE_OPEN_REQUIRED`).
#:
#: ⚠️ **`day_gate` 와 같은 수여야 한다.** 관문이 *"관리자 강제 개장이 필요하다"* 고
#:    말했는데 눌러도 안 열리면 화면이 왜인지 못 말한다.
MAX_FORCE_CARRY_DAYS = 366


# ── 달력을 걷는다 ───────────────────────────────────────────────────────


def _walk_part(
    part: DayOpenPart, impl: Any, conn: Any, *, as_of: date, limit: int = MAX_CARRY_DAYS
) -> DayOpenPartOut:
    """한 파트의 달력을 걷는다. **구멍을 남기지 않는다.**

    ```text
    ① as_of 부터 하루씩 뒤로 가며 is_open 이 참인 날을 찾는다 (최대 31일)
    ② 못 찾으면 막고 사유를 낸다 — 행을 지어내지 않는다
    ③ 찾으면 그 다음 날부터 as_of 까지 하루씩 open_day(as_of=d, carry_from=d-1)
    ```

    ★ **마지막 행이 12-31 이고 `as_of` 가 01-05 면 다섯 행을 만든다** — 01-01 · 01-02 ·
      01-03 · 01-04 · 01-05. 01-03 을 건너뛰면 나중에 그날을 조회할 때 또 막힌다.

    🔴 **달력은 날마다다. 실행일이 아니다.** 주말·공휴일도 채운다.
       `execution_day.next_execution_day` 를 **쓰지 않는다** — 판단은 평일만이고
       장부는 날마다다. 토요일에도 재고는 늙고 지급일은 온다 (`#240` · `#242` 가
       정한 *"실행일은 평일만, 경과일수는 달력일"* 과 같은 결).
    """
    anchor: date | None = None
    for back in range(limit + 1):
        day = as_of - timedelta(days=back)
        if impl.is_open(conn, as_of=day):
            anchor = day
            break

    if anchor is None:
        # ★ **막는다.** 여기서 상한을 넘겨 걸으면 수백 행이 조용히 생긴다.
        #
        # 🔴 `gap_days` 를 채운다 — 마스터가 이 칸을 보고 전체를 `REJECTED_GAP` 으로
        #    올린다. 사유 문자열을 읽지 않는다.
        return DayOpenPartOut(
            part=part,
            status="PART_FAILED",
            gap_days=limit,
            reason=(
                f"{as_of} 부터 {limit}일 뒤로 가도 열린 날이 없다 —"
                " 상한을 넘는 것은 의도보다 실수일 가능성이 크다. 행을 만들지 않는다"
            ),
        )

    opened: list[date] = []
    day = anchor + timedelta(days=1)
    try:
        while day <= as_of:
        # 🔴 `carry_from` 은 **언제나 바로 전날**이다. 건너뛴 날에서 물려받으면 그
        #    사이 하루치 사실이 장부에 없는 채로 다음 행이 선다.
            impl.open_day(conn, as_of=day, carry_from=day - timedelta(days=1))
            opened.append(day)
            day += timedelta(days=1)
    except Exception as exc:  # noqa: BLE001 - 파트 실패를 파트 어휘로 옮긴다.
        # 🔴 **예외를 밖으로 내보내면 `parts` 가 비어 화면이 어느 파트인지 모른다.**
        #
        #    전에는 그랬다 — `open_day` 의 바깥 `except` 가 잡아 전체 `reason` 에
        #    메시지만 뭉쳐 넣었고, `PART_FAILED` 경로는 **gap 으로만** 도달할 수
        #    있었다(그리고 그건 `REJECTED_GAP` 으로 올라간다). 변이로 찾은 자리다.
        #
        # ★ **계약이 `PART_FAILED.reason` 은 화면까지 간다고 했다** (§6). 그러려면
        #   어느 파트가 왜 못 열었는지가 파트 결과로 남아야 한다.
        #
        # ⚠️ **롤백은 그대로다.** 이 함수는 사유만 옮기고, 되돌리는 것은 `open_day`
        #   가 한다 — 한 파트가 실패하면 다른 파트가 만든 행도 되돌린다.
        return DayOpenPartOut(
            part=part,
            status="PART_FAILED",
            reason=f"{type(exc).__name__}: {exc}",
            opened=opened,
        )

    if not opened:
        # ★ **`PART_ALREADY_OPENED` 다.** anchor 가 `as_of` 자신이라 만들 날이 없었다 —
        #   *"할 일이 없었다"* 이지 *"못 했다"* 가 아니다 (계약 §어휘 · 멱등 no-op).
        return DayOpenPartOut(part=part, status="PART_ALREADY_OPENED")
    return DayOpenPartOut(part=part, status="PART_OPENED", opened=opened)


# ── 트랜잭션 경계 ───────────────────────────────────────────────────────


def open_day(
    as_of: date,
    *,
    borrow: core_db.Borrow | None = None,
    force: bool = False,
    seed_collection: Callable[..., CollectionSeedOutcome] = seed_day,
    sim_run_id: str,
) -> DayOpenOut:
    """`as_of` 날 상태 행을 **파트마다** 보장한다. 한 트랜잭션이다.

    순서가 이 함수의 전부다.

    ```text
    1. 미등록 확인   → 등록이 0건이면 커넥션을 열지 않는다
    2. 파트마다 걷기 → 한 커넥션으로 · 파트끼리 서로를 되돌리지 않는다
    3. commit 한 번  → 실패하면 rollback
    ```

    ★ **파트마다 따로 걷는다** (C.1). 재무와 물류가 서로 다른 날까지 열려 있을 수
      있고, 한 파트가 뒤처졌다고 다른 파트를 되돌리지 않는다.

    ★ **멱등이다** (C.5). 같은 날을 두 번 열면 두 번째는 아무것도 안 한다 —
      `opened` 가 빈 목록으로 나가는 것이 *"이미 열려 있었다"* 다.

    ★ **미등록은 오류가 아니라 상태다** (`transition.py` · `wiring.py` 와 같은 태도).
      등록이 0건이면 커넥션을 열지 않고 사유와 함께 돌아선다. 한쪽만 등록돼 있으면
      **등록된 쪽만 걷는다** — 두 파트가 서로의 트랜잭션에 얹혀 있지 않기 때문이다
      (`apply_approval` 이 반쪽 반영을 막는 것과 다른 자리다).

    🔴 **예외를 밖으로 던지지 않는다.** 하루 넘김이 500 을 내면 사람이 보기에는 다음
       날로 갈 수 없는 것이 되는데, 실제로는 롤백되어 어제 그대로다. **다만 삼키되
       사유는 반드시 남긴다.**

    🔴 **`force` 는 상한 하나만 푼다** (계약 §5).

      ```text
      강제 개장이 푸는 것    31일 상한 → 366일
      강제 개장이 못 푸는 것 PART_FAILED · 366일 초과
      ```

      ★ **실패를 성공으로 승격시키는 문이 아니다.** 강제 개장 중에도 한 파트가
        실패하면 전체는 계속 `NOT_OPENED` 다.

      ★ **파트는 강제인지 아닌지를 모른다.** Protocol 이 안 바뀌고 평소와 똑같이
        답한다 — 마스터가 상한만 풀고 나머지는 동일하게 취합한다.

      ⚠️ **366일을 넘기면 강제로도 안 열린다.** 관리자가 눌러도 안 열리는 것을
        `ADMIN_FORCE_OPEN_REQUIRED` 로 보내면 화면이 왜인지 못 말하므로,
        `day_gate` 가 그 경우를 `SPLIT_FORCE_OPEN_REQUIRED` 로 따로 낸다.

    🔴 **개장이 성공한 뒤 그날 결제기일인 채권을 수금 사건으로 옮긴다** (재무 조건 `⑥`).

      ★ **그 결과가 개장을 실패시키지 않는다.** 사건 생성이 터져도 하루는 열려야 하고,
        *"못 했다"* 는 `collection_seed_status` 로만 실린다.

    :param borrow: 연결을 빌려 주는 함수(`with borrow() as conn:` 끝에 돌려준다). 안 주면
                    `app.core.db.connection`(공통 풀) — 재무·물류가 같은 DB(같은 `DB_*`)를
                    쓰므로 연결도 하나면 된다. commit · rollback 은 여기서 눈에 보이게 한다.
    :param force: 관리자 강제 개장. **상한만 푼다.**
    :param seed_collection: 수금 사건을 만드는 방법. 기본값이 `collection_seed.seed_day`
                    이고, 검사가 대역을 끼울 자리다. **파트 트랜잭션 밖에서** 돈다.
    :param sim_run_id: 어느 실행의 장부를 넘기는가 (`#531` 후속). 🔴 **기본값이 없다**
                    (2026-09-14) — `apply_approval` 과 같다. 번인 상수로 메우면
                    축을 안 준 호출이 번인 장부를 연다. 걷기는 `run_scheduled_day`
                    가, 라우터(`POST /master/days/{as_of}/open`)는 요청이 준 축을 싣는다.

                    ★ **새 행에 적는 값이 아니다.** carry-forward 는 전날 행의
                      `sim_run_id` 를 그대로 옮긴다 — 이 값이 정하는 것은 *"어느 전날
                      행을 물려받는가"* 다 (`#324` 가 `bootstrap.py` 에 적어 둔 그것).
    """
    absent = missing()
    openings = registered()
    present = [part for part in PARTS if part in openings]
    if not present:
        # ★ 여기서 돌아선다 — **커넥션을 열지 않는다.** 열고 나서 아무 일도 안 하면
        #   빈 트랜잭션이 하루 넘김마다 열렸다 닫힌다.
        out = DayOpenOut(
            as_of=as_of,
            status="NOT_OPENED",
            reason=f"하루 넘김 미등록: {', '.join(absent)}",
            missing=list(absent),
        )
        # ★ **미등록도 남긴다.** *"안 열렸다"* 는 사실이고, 화면이 그 날을 지나가지
        #   않으려면 정본에 있어야 한다.
        out = _seed_collection(out, seed_collection, sim_run_id=sim_run_id)
        _record(out, sim_run_id=sim_run_id)
        return out

    open_connection = core_db.connection if borrow is None else borrow
    with open_connection() as conn:
        try:
            limit = MAX_FORCE_CARRY_DAYS if force else MAX_CARRY_DAYS
            # 🔴 **등록소가 든 축이 아니라 이번 하루의 축으로 묶는다** (`#531` 후속).
            #    물류 `LogisticsDayOpening` 은 그 축으로 전날 행을 좁혀 읽는다 —
            #    `uq_log_runtime_fixture` 가 `(sim_run_id, as_of, usage_scope)` 라
            #    안 좁히면 **남의 실행 행을 보고 "열렸다"** 고 답한다 (`#324`).
            parts = [
                _walk_part(
                    part, bind_sim_run(openings[part], sim_run_id), conn, as_of=as_of, limit=limit
                )
                for part in present
            ]
            if any(part.status == "PART_FAILED" and part.gap_days is None for part in parts):
                # 🔴 **파트가 터지면 전체를 되돌린다.** 다른 파트가 만든 행도 되돌린다 —
                #    반쯤 만들다 만 행이 남으면 다음 날이 그 위에 선다.
                conn.rollback()
                # 🔴 **`opened` 를 비운다. 되돌렸으니 그 행들은 없다.**
                #
                #    남겨 두면 화면이 *"이 날들을 만들었다"* 로 읽는데 DB 에는 없다 —
                #    `parts` 자체는 남긴다. 어느 파트가 **왜** 못 열었는지가 계약상
                #    화면까지 가야 하기 때문이다 (§6).
                parts = [part.model_copy(update={"opened": []}) for part in parts]
            else:
                # ★ **상한 초과(`gap_days`)는 롤백이 아니다.** 그 파트는 **아무것도 안
                #   만들었고**, 다른 파트가 만든 행은 살려야 한다 — *"한 파트가 뒤처졌다고
                #   다른 파트를 되돌리지 않는다"* (계약 C.1).
                conn.commit()
        except Exception as exc:  # noqa: BLE001 - 하루 넘김 실패가 500 으로 올라가면 안 된다.
            conn.rollback()
            # ⚠️ 계약 어휘에 `FAILED` 가 없다 — *"한 파트라도 실패하면 전체는
            #    `NOT_OPENED`"* 가 계약이고 커밋 실패도 *"못 열었다"* 다. 무엇이 터졌는지는
            #    `reason` 이 나른다.
            out = DayOpenOut(
                as_of=as_of,
                status="NOT_OPENED",
                reason=f"하루 넘김 실패: {exc}",
                missing=list(absent),
            )
            out = _seed_collection(out, seed_collection, sim_run_id=sim_run_id)
            _record(out, sim_run_id=sim_run_id)
            return out

    out = _aggregate(as_of, parts, absent, force=force)
    # 🔴 **개장이 성공한 뒤에 부른다** (재무 조건 `⑥`).
    #
    #    조건 `⑥` 이 *"개장 시 대상 채권을 확인해 아직 event 가 없는 건만 생성"* 이라고
    #    못 박았다. **15건만 손으로 채우는 방식은 받기 어렵다** — 새 Receivable 이
    #    생기면 그날 개장이 그것을 집어 온다.
    out = _seed_collection(out, seed_collection, sim_run_id=sim_run_id)
    _record(out, sim_run_id=sim_run_id)
    return out


def _seed_collection(
    out: DayOpenOut,
    seed_collection: Callable[..., CollectionSeedOutcome],
    *,
    sim_run_id: str,
) -> DayOpenOut:
    """열린 날의 수금 사건을 만든다. **개장을 실패시키지 않는다.**

    🔴 **하루가 안 열렸으면 시도하지 않는다.** `NOT_OPENED` · `REJECTED_GAP` 인 날에
      사건을 만들면 서 있지도 않은 재무 상태 행에 대고 수금을 적는 셈이 된다.

    🔴 **`NOT_ATTEMPTED` 를 `NOTHING_DUE` 로 접지 않는다.** *"안 했다"* 와 *"확인했고
      없었다"* 는 다른 사실이고, 접으면 개장이 막힌 날이 *"오늘은 낼 것이 없었다"* 로
      읽힌다.

    ★ **사건 생성이 터져도 하루는 열려 있다.** `seed_day` 가 예외를 안 내보내지만,
      그것을 여기서 다시 한 번 잡는다 — 개장의 성공 여부가 이 줄에 걸리면 안 된다.
    """
    if out.status not in ("OPENED", "ALREADY_OPENED"):
        return out.model_copy(
            update={
                "collection_seed_status": "NOT_ATTEMPTED",
                "collection_seed_reason": f"하루가 안 열렸다: {out.status}",
            }
        )
    try:
        # 🔴 **개장이 받은 축을 그대로 쓴다** (`#531` 후속). 여기만 상수로 남기면
        #    하루는 걷기 실행에 열리는데 그날 수금 사건은 번인에 앉는다.
        결과 = seed_collection(out.as_of, sim_run_id=sim_run_id)
    except Exception as exc:  # noqa: BLE001 - 개장이 이 줄 때문에 죽으면 안 된다.
        결과 = CollectionSeedOutcome(
            status="UNREADABLE",
            reason=f"수금 사건을 만들지 못했다: {type(exc).__name__}: {exc}",
        )
    return out.model_copy(
        update={
            "collection_seed_status": 결과.status,
            "collection_seeded": 결과.created,
            "collection_seed_skipped": 결과.skipped,
            "collection_seed_reason": 결과.reason,
        }
    )


def _record(out: DayOpenOut, *, sim_run_id: str) -> None:
    """개장 정본에 남긴다. **파트 트랜잭션 밖이다.**

    🔴 **실패도 남아야 시도 횟수를 셀 수 있다.** 파트 트랜잭션 안에 넣으면 롤백될 때
       *"실패했다는 사실"* 까지 사라지고, 그러면 `day_gate` 가 재시도와 사람을 못 가른다.

    ★ **적재 실패가 개장을 죽이지 않는다.** 이력이 없는 것보다 하루를 못 여는 것이
      나쁘다 (`try_save_run` 과 같은 판단).
    """
    record_day_opening(
        as_of=out.as_of,
        # 🔴 **개장이 받은 축이다** (`#531` 후속). 정본 행이 번인에 앉으면
        #    `day_gate` 가 걷기 실행의 그날을 **안 열린 날**로 읽는다.
        sim_run_id=sim_run_id,
        result=out.status,
        reason=out.reason,
        parts=out.parts,
    )


def _aggregate(
    as_of: date, parts: list[DayOpenPartOut], absent: tuple[str, ...], *, force: bool = False
) -> DayOpenOut:
    """파트 결과를 **전체 어휘 넷**으로 취합한다 (계약 §어휘).

    ```text
    gap_days 가 있는 파트가 하나라도  → REJECTED_GAP   (상한을 넘겼다)
    PART_FAILED 가 하나라도            → NOT_OPENED     (한 파트라도 실패하면 전체가)
    PART_OPENED 가 하나라도            → OPENED
    전부 PART_ALREADY_OPENED           → ALREADY_OPENED
    ```

    🔴 **순서가 계약이다.** `REJECTED_GAP` 을 먼저 보는 이유는 그것만 *"관리자 강제
       개장"* 이라는 다른 다음 걸음을 갖기 때문이다 — `NOT_OPENED` 로 접으면 화면이
       재시도를 권하고, 재시도로는 안 풀린다.
    """
    gapped = [part for part in parts if part.gap_days is not None]
    if gapped:
        names = ", ".join(part.part for part in gapped)
        limit = MAX_FORCE_CARRY_DAYS if force else MAX_CARRY_DAYS
        # ⚠️ **강제 개장이었으면 그것도 적는다.** 관리자가 눌렀는데 또 거절당한 것이라
        #    다음 걸음이 다르다 — 나눠서 불러야 한다.
        꼬리 = " — 강제 개장으로도 못 연다. 나눠서 불러야 한다" if force else ""
        return DayOpenOut(
            as_of=as_of,
            status="REJECTED_GAP",
            reason=f"상한({limit}일)을 넘겨 거절했다: {names}{꼬리}",
            parts=parts,
            missing=list(absent),
        )
    failed = [part for part in parts if part.status == "PART_FAILED"]
    if failed:
        # 🔴 **파트 사유를 전체 사유에 싣는다** (계약 §6 — 화면까지 나간다).
        #    파트 이름만 적으면 *"logistics 가 막혔다"* 로 끝나고, **무엇이 막았는지**를
        #    화면이 못 말한다.
        말 = "; ".join(
            f"{part.part}: {part.reason}" if part.reason else part.part for part in failed
        )
        return DayOpenOut(
            as_of=as_of,
            status="NOT_OPENED",
            reason=f"막혔다 — {말}",
            parts=parts,
            missing=list(absent),
        )
    if any(part.status == "PART_OPENED" for part in parts):
        return DayOpenOut(as_of=as_of, status="OPENED", parts=parts, missing=list(absent))
    return DayOpenOut(
        as_of=as_of,
        status="ALREADY_OPENED",
        reason="이미 열려 있었다 — 만든 행이 없다",
        parts=parts,
        missing=list(absent),
    )


logger = logging.getLogger(__name__)


def record_day_opening(
    *,
    as_of: date,
    sim_run_id: str,
    result: str,
    reason: str = "",
    parts: Sequence[Any] = (),
    borrow: core_db.Borrow | None = None,
) -> bool:
    """개장 1회를 정본에 적는다. **예외를 올리지 않는다.**

    ```text
    처음이면        attempt_count=1 · failure_count = 성공이면 0 아니면 1
    다시 부르면      attempt_count += 1
                    성공이면 failure_count = 0
                    실패면   failure_count += 1
    ```

    🔴 **성공이 연속 실패를 0 으로 되돌린다.** 그래야 *"어제 성공하고 오늘 처음 실패"*
       가 첫 실패로 세어지고, 재시도를 한 번은 권하게 된다.

    ⚠️ **`parts` 원문을 그대로 담는다.** `PART_FAILED` 의 내부 상세는 여기에만 남고
      화면에 안 간다 (계약 §6). 마스터가 그것을 해석하지 않는다.

    :returns: 적었으면 참. **못 적어도 거짓을 돌려줄 뿐 개장을 죽이지 않는다.**
    """
    payload = [
        part.model_dump(mode="json") if hasattr(part, "model_dump") else part for part in parts
    ]
    # ★ 종전처럼 문장(스키마 이름)을 연결보다 먼저 짓는다 — `DB_SCHEMA` 가 없으면 아래 삼킴 블록
    #   밖에서 오류가 난다(종전과 같은 실패 경로).
    schema = get_db_schema()

    open_connection = core_db.connection if borrow is None else borrow
    with ExitStack() as stack:
        try:
            conn = stack.enter_context(open_connection())
        except Exception:
            logger.exception("개장 정본 커넥션 실패 - 개장 결과는 그대로 나간다")
            return False
        try:
            upsert_day_opening(
                conn,
                as_of=as_of,
                sim_run_id=sim_run_id,
                result=result,
                reason=reason,
                parts_payload=payload,
                schema=schema,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            logger.exception("개장 정본 적재 실패 - 개장 결과는 그대로 나간다")
            return False
    return True
