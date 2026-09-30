"""ML 백엔드 HTTP 호출 — 이 서버가 우리 ML 백엔드(기본 8102)에 묻는 자리는 여기 하나다.

```text
forward(method, path, …)   운영 콘솔 프록시(`/ml/console/…`)가 받은 요청을 그대로 넘긴다 (비동기)
retrain_pending()          채팅 «모델 성능» 갈래가 «사람이 눌러야 할 재학습 결정» 을 묻는다 (동기)
console_origin()           두 호출이 함께 쓰는 ML 백엔드 주소 (`ML_CONSOLE_ORIGIN`)
```

★ **SQL 이 아니라 HTTP 다.** 재학습 · 에이전트 · 설명은 ML 저장소가 있는 곳에서만 돌 수 있어
  (`app/api/ml/console.py` 머리말 «왜 프록시인가»), 그쪽 답은 우리 DB 에 없다. 그래서 repository 가
  아니라 이 파일이다.

★ **HTTP 상태 코드를 정하지 않는다.** 닿지 못했거나(`MlBackendUnreachable`) 제때 답이
  없으면(`MlBackendTimeout`) 그 사실을 예외로 올리고, 502 · 504 로 바꾸는 것은 라우트
  (`app/api/ml/console.py`)다.
  저쪽이 낸 상태와 본문은 **그대로** 돌려준다.

🟢 **자리 (2026-09-29 · 재구성 BL-017 · 설계서 쟁점 12 배치안).** 전에는 라우트 파일
  `console_proxy.py` 가 주소 · 전달을, `qa_tools.py` 가 `retrain_pending` 을 들고 있었고,
  `qa_tools.py` 가 주소를 얻으려고 라우트 모듈을 import 했다. 주소 규칙 · 타임아웃 · 헤더 ·
  실패 구분은 그대로다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv

from app.core.settings import ENV_FILE

#: 같은 컴퓨터에서 돌 때의 기본값.
_DEFAULT_ORIGIN = "http://127.0.0.1:8102"


def console_origin() -> tuple[str, str]:
    """ML 백엔드 주소와, 그 주소를 어디서 가져왔는지.

    ★ **이름이 공개다.** 프록시 말고도 서버 안에서 ML 콘솔에 직접 묻는 곳이
      생겼다 — 채팅의 «모델 성능» 갈래가 `retrain/pending` 을 읽는다
      (`retrain_pending`). 주소를 정하는 규칙이 두 군데로 갈리면
      한쪽만 고쳐 놓고 «왜 저기서는 되는데 여기서는 안 되나» 가 된다.

    ★ **`.env` 를 여기서 직접 읽는다** (2026-09-10 고침).

      이 저장소는 `.env` 를 **모듈마다 따로** 읽는다 (`app/ml/db.py` ·
      `app/finance/db.py` 가 각자 `load_dotenv` 를 부른다). 그런데 여기서는
      안 불렀고, `os.getenv` 를 **모듈을 불러오는 시점에** 한 번만 봤다.
      그 시점에는 아무도 `.env` 를 안 읽었을 수 있다 — 부르는 순서에 달렸다.

      그래서 `.env` 에 `ML_CONSOLE_ORIGIN` 을 바르게 적어도 **읽히지 않고**
      기본값 `127.0.0.1` 로 떨어졌다. ML 서버가 다른 컴퓨터에 있으면
      자기 안에서 찾다가 502 가 난다.

      ★ 같은 컴퓨터에서 돌릴 때는 기본값이 우연히 맞아서 **아무 증상이
        없었다.** 다른 컴퓨터를 붙이는 날 처음 드러났다.

      요청마다 읽는다. `load_dotenv` 는 이미 있는 환경변수를 덮지 않고,
      한 번 읽은 뒤로는 값이 환경에 남으므로 파일을 매번 훑지 않는다.
    """
    load_dotenv(ENV_FILE, override=False)
    got = os.getenv("ML_CONSOLE_ORIGIN")
    if got:
        return got.rstrip("/"), "이 주소는 backend/.env 의 ML_CONSOLE_ORIGIN 에서 왔습니다."
    #   ★ 「서버가 떠 있나」만 물으면, 정작 서버는 떠 있는데 주소가 틀린
    #     경우에 엉뚱한 데를 뒤지게 된다. 실제로 그랬다 (2026-09-10).
    return _DEFAULT_ORIGIN, (
        "★ backend/.env 에 ML_CONSOLE_ORIGIN 이 없어 기본값을 썼습니다. "
        "ML 서버가 다른 컴퓨터에 있으면 그 주소를 적으세요 "
        "(예: ML_CONSOLE_ORIGIN=http://192.168.0.x:8102) — .env 는 깃에 안 올라가므로 "
        "컴퓨터마다 따로 적어야 합니다."
    )


# ── 운영 콘솔 프록시 ─────────────────────────────────────────────────────

#: 얼마나 기다리나. 재학습 후보 만들기는 몇 분 걸리므로 길게 둔다 —
#: 다만 그 호출은 **바로 돌아오고 진행은 따로 물어보는** 구조라
#: 실제로 오래 걸리는 요청은 없다.
TIMEOUT = httpx.Timeout(connect=3.0, read=30.0, write=10.0, pool=3.0)


@dataclass(frozen=True)
class BackendReply:
    """ML 백엔드가 낸 상태와 본문. **바꾸지 않고 그대로 담는다.**"""

    status_code: int
    body: Any


class MlBackendUnreachable(Exception):
    """ML 백엔드에 닿지 못했다 (`httpx.ConnectError`). 라우트가 502 로 바꾼다."""

    def __init__(self, origin: str, hint: str, cause: httpx.ConnectError) -> None:
        super().__init__(str(cause))
        self.origin = origin
        self.hint = hint
        self.cause = cause


class MlBackendTimeout(Exception):
    """ML 백엔드가 제때 답하지 않았다 (`httpx.TimeoutException`). 라우트가 504 로 바꾼다."""

    def __init__(self, origin: str, path: str, cause: httpx.TimeoutException) -> None:
        super().__init__(str(cause))
        self.origin = origin
        self.path = path
        self.cause = cause


async def forward(
    method: str, path: str, *, params: dict[str, str], body: bytes | None
) -> BackendReply:
    """받은 요청을 ML 백엔드의 같은 경로로 넘기고 답을 그대로 돌려준다.

    ★ 쓰기(POST)는 본문을 그대로 싣고 `Content-Type: application/json` 을 붙인다. 조회(GET)는
      본문 · 머리글 없이 쿼리만 넘긴다. **인증 머리글은 없다** — 종전에도 넘기지 않았다.
    """
    origin, hint = console_origin()
    url = f"{origin}/{path}"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.request(
                method, url,
                params=params,
                content=body if method == "POST" else None,
                headers={"Content-Type": "application/json"} if method == "POST" else None,
            )
    except httpx.ConnectError as error:
        #   ★ 여기서 조용히 빈 값을 돌려주면 화면이 «자료가 없다» 로 보인다.
        #     ML 백엔드가 안 떠 있다는 사실 자체를 알려야 한다.
        raise MlBackendUnreachable(origin, hint, error) from error
    except httpx.TimeoutException as error:
        raise MlBackendTimeout(origin, path, error) from error

    #   저쪽이 낸 상태와 본문을 **그대로** 올린다. 400 을 200 으로 바꾸면
    #   무엇을 잘못 넣었는지가 사라진다.
    try:
        content = response.json()
    except ValueError:
        content = {"detail": response.text[:500]}
    return BackendReply(status_code=response.status_code, body=content)


# ── 바꿀 모델이 있나 ──────────────────────────────────────────────────
#
# 🔴 **이것만 DB 가 아니라 HTTP 다.** 나머지는 전부 SELECT 인데, 이 물음의 답은
#   우리 DB 에 없다. 재학습 그래프의 체크포인트와 배치가 남긴 파일이 ML 저장소
#   쪽에 있고, 둘을 **겹쳐야** 답이 나온다.
#
#   ```text
#   파일(_retrain_pending.json)   무엇을 견줬나 — 배치가 찍어 둔 그때의 사진
#   그래프 체크포인트              아직 사람 답을 기다리나 — 지금 이 순간
#   ```
#
#   사람이 「모델 업데이트」 를 누르면 **그래프만 바뀌고 파일은 그대로 남는다.**
#   파일만 보면 누른 뒤에도 계속 «바꿔야 합니다» 가 뜬다 — 실제로 그랬고
#   ML 쪽 `/retrain/pending` 이 2026-09-09 에 그것을 고쳤다. 그 창구를 그대로
#   쓴다. 여기서 다시 판정하지 않는다.
#
#   그래서 `agent_report` 로는 대신할 수 없다. 보고서는 «후보가 낫다» 까지만
#   말하고 «아직 안 눌렀다» 는 모른다.

#: **짧게 기다린다.** 이건 사람이 채팅에서 답을 기다리는 길목이다. ML 콘솔이
#: 안 떠 있을 때 30초를 붙들면 답 전체가 그만큼 늦는다 — 못 읽으면 한 줄로
#: 말하고 넘어가는 편이 낫다 (`_perf_section`).
PENDING_TIMEOUT = httpx.Timeout(connect=2.0, read=4.0, write=2.0, pool=2.0)

#: 사람이 눌러야 할 결정이 있는 가격 종류. **여기 없는 값은 버린다** —
#: 버튼을 만들 수 없는 종류를 답에 적으면 누를 수 없는 버튼이 나간다.
PENDING_KINDS = ("auc", "whsl", "rtl")


def retrain_pending() -> list[dict[str, Any]]:
    """사람이 눌러야 할 재학습 결정. 없으면 빈 목록, **못 읽으면 터진다.**

    🔴 **못 읽은 것을 «없다» 로 돌려주지 않는다.** 둘은 다른 사실이고, 답에
      적는 말도 다르다 — 앞은 «확인 불가», 뒤는 «후보 없음» 이다. 여기서
      섞으면 ML 콘솔이 죽어 있는 동안 화면이 «바꿀 것 없음» 으로 보인다.
    """
    origin, _hint = console_origin()
    with httpx.Client(timeout=PENDING_TIMEOUT) as client:
        response = client.get(f"{origin}/retrain/pending")
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise TypeError(f"/retrain/pending 이 사전이 아닌 것을 줬습니다: {type(body)}")
    return [
        row for row in (body.get("pending") or [])
        if isinstance(row, dict) and str(row.get("kind") or "").lower() in PENDING_KINDS
    ]
