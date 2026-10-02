"""walk_provenance.py — 걷기가 받은 `--now` 를 실행 행에 남긴다.

```text
record_walked_now(sim_run_id=..., walked_now=...)   커넥션을 열고 · 적고 · 커밋한다
stamp_walked_now(conn, sim_run_id=..., walked_now=...)  부르는 쪽 커넥션으로 잰다·적는다
```

```json
{"baseline": {...}, "backfill": {...},
 "provenance": {"commit": "0e635f8", "walked_now": "2026-09-13T16:00+09:00"}}
```

이 칸이 있는 이유: 걷기는 `--now` 의 시각 부분만 날마다 붙여 쓰고
(`report/walk_summary.py` 의 `moment_on`), 마감(10:30) 전 시각을 주면 예측이 아직 안 온
날이 `WAIT` 로 안 돈다. 그 값이 어디에도 안 남으면 같은 코드로 건 두 실행(V8 16:00 ·
V9① 09:00)이 통째로 갈린 이유가 코드가 아니라 입력 하나였다는 것을 원장에서 읽을 수
없다. 칸이 아니면 없는 것과 같다(`#622` 의 기준 커밋과 같은 결).

걷기가 쓰고, 문(`sim_run_runner`)은 받지 않는다. 진짜 값을 아는 것은 걷기뿐이다. 여는
자리에서 받으면 실제로 건 값과 갈릴 수 있고, 그러면 칸이 거짓말을 한다.

`provenance.commit` 은 건드리지 않는다. 그건 여는 자리 것이다. 그래서 칸을 통째로 덮지
않고 한 키만 붙인다(`jsonb_set` · `||`).

값은 받은 문자열 그대로 싣고 정규화하지 않는다. 사람이 준 것과 코드가 쓰는 것이
갈려야 왜 갈렸는지가 읽힌다 — 여기서 `isoformat()` 으로 다시 쓰면 사람이 준 것이
사라진다.

이미 다른 값이 있으면 막는다.

```text
없으면          → 쓴다
같은 문자열이면 → 그대로 간다 (이어 걷기는 정상이다)
다르면          → 걷기 전에 멈추고 사유를 낸다
```

한 실행을 두 기준 시각으로 이어 걸으면 그 실행의 절반은 마감 전, 절반은 마감 뒤가
된다. 그 실행은 아무것도 증명하지 않는다. 다른 시각으로 다시 걸려면
`sim_run_runner --reset` 으로 새로 열면 된다. 덮어쓰기 옵션은 만들지 않는다 — 이
저장소에 `--force` 류가 없는 이유와 같다.

잠금: 잴 때 행을 잠근다(`FOR UPDATE`). 두 걷기가 같은 실행에 동시에 들어오면 둘 다
「없다」를 보고 각자 적을 수 있다 — 재는 것과 쓰는 것이 한 트랜잭션이다. SQL 은
`repository/walk_provenance.py` 에 있다.
"""

from __future__ import annotations

from contextlib import suppress

from app.core import db as core_db
from app.master.repository.walk_provenance import WalkedNowStamp, stamp_walked_now


def record_walked_now(*, sim_run_id: str, walked_now: str) -> WalkedNowStamp:
    """공통 풀에서 연결을 빌려 `stamp_walked_now` 를 부르고 한 번 커밋한다. 걷기의 기본값이다.

    실패 처리: 막히면 롤백한다. 잰 것만 있고 쓴 것이 없어도 행 잠금은 풀어야 한다.
    """
    with core_db.connection() as conn:
        try:
            stamp = stamp_walked_now(conn, sim_run_id=sim_run_id, walked_now=walked_now)
        except Exception:
            with suppress(Exception):
                conn.rollback()
            raise
        else:
            conn.commit()
            return stamp
