"""마스터 ↔ 화면(`app.api`) 경계와 새 domain 의 의존 (재구성 BL-012 · 설계서 §의존 방향 규칙).

2026-09-29 전에는 두 방향으로 엉켜 있었다.

```text
마스터 → 화면   master/ask_service.py   → api/shown_run.SHOWN_SIM_RUN_ID
                master/report.py        → api/logistics/query._still_working (함수 안 import)
화면 → 마스터   api/dashboard · purchase · plan_state → master/purchase_record_repository
```

옮긴 자리: 보는 실행 → `core/settings.py`, 예약 · 우선도 판정 → `logistics/domain/
console_rules.py`, 매입안 상태 → `master/domain/plan_state.py`, 실매입 합계 조립 →
`master/readmodel/purchase_record.py`. 이 검사가 그 결과를 잠근다.

★ 스캐너(`import_scan.py`)는 함수 안 · 문자열 동적 import 까지 본다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from import_scan import imports_of, is_under, module_refs, python_files

import app

_APP = Path(app.__file__).parent

#: 화면이 마스터에서 들여도 되는 자리 — 조회 결과(readmodel)와 순수 판정(domain).
#: repository(SQL) · service(쓰기 순서) · 등록소는 화면이 직접 부르지 않는다.
API_MAY_TAKE_FROM_MASTER = ("app.master.readmodel", "app.master.domain")

#: 새로 세운 domain 폴더. 입력 → 출력만 — DB · HTTP · 화면 · 조회 계층을 모른다.
DOMAIN_DIRS = ("logistics/domain", "master/domain")

#: domain 이 들여도 되는 앱 모듈 — 설계서 §계층 책임 표 `domain` 행(schemas · contracts ·
#: core.text). 부서의 `schemas`(모듈이든 패키지든)와 같은 `domain` 계층은 아래 검사가 이름
#: 조각으로 받는다 — 2026-09-30 재구성 BL-015 부터 물류 `schemas` 는 패키지이고 물류 domain
#: 파일끼리 순수 함수를 서로 부른다.
_DOMAIN_MAY_TAKE = ("app.contracts", "app.core.text")
#: 모듈 전체가 아니라 이름 하나만 들여도 되는 자리 — 시간대 상수는 시계 함수가 아니다
#: (물류 domain 의 날짜 경계 계산 · 2026-09-30 재구성 BL-015).
_DOMAIN_MAY_TAKE_NAMES = {"app.core.clock": frozenset({"SEOUL"})}
#: 표준 라이브러리 중 부작용(네트워크 · 파일 DB · 프로세스)을 여는 것.
_SIDE_EFFECT_STDLIB = ("urllib", "http", "socket", "sqlite3", "subprocess", "smtplib")


def _files(sub: str) -> list[Path]:
    return python_files(_APP / sub)


def _rel(path: Path) -> str:
    return path.relative_to(_APP).as_posix()


# ── 마스터 → 화면 0 ──────────────────────────────────────────────────────


def test_master_does_not_import_screen_api():
    found = {
        _rel(path): hits
        for path in _files("master")
        if (hits := imports_of(path.read_text(encoding="utf-8"), "app.api"))
    }

    assert found == {}, (
        "마스터가 화면 모듈을 들인다 — 공유 설정이면 `app/core/settings.py`, 판정이면 부서"
        f" domain, 조회면 부서 readmodel 로 옮긴다: {found}"
    )


def test_scanner_reads_master_files():
    """★ 0 개를 읽으면 위 검사가 공짜 초록이 된다. 무엇을 읽었는지를 먼저 잰다."""
    names = {_rel(path) for path in _files("master")}

    # ★ 2026-09-30 재구성 BL-018: 채팅 실행 `master/service/ask.py` · 채팅 보고서
    #   `master/report/chat_reports.py`.
    assert {
        "master/service/ask.py", "master/report/chat_reports.py", "master/domain/plan_state.py"
    } <= names
    assert len(names) > 80, f"마스터 파일을 {len(names)}개밖에 못 읽었다"


def test_scanner_catches_hidden_screen_import_forms():
    """심어 둔 여섯 모양이 다 잡히고, 설정 import 와 주석 · 문구 언급은 안 잡힌다."""
    planted = (
        "import app.api.shown_run as s\n"
        "from app import api\n"
        "from app.api.shown_run import SHOWN_SIM_RUN_ID\n"
        "def later():\n"
        "    from app.api.logistics.query import _still_working\n"
        "    importlib.import_module('app.api.logistics.query')\n"
        "    __import__('app.api')\n"
    )

    assert len(imports_of(planted, "app.api")) == 6, imports_of(planted, "app.api")
    assert imports_of(
        "from app.core.settings import SHOWN_SIM_RUN_ID\n# app.api 를 말만 한다\n"
        "x = 'app/api/shown_run.py 에 있었다'\n",
        "app.api",
    ) == []


# ── 화면 → 마스터: readmodel · domain 만 ──────────────────────────────────


#: 마스터 · Critic 의 HTTP 입구 — 화면이 아니라 마스터 자기 라우트다(2026-09-30 재구성 BL-019 에
#: `app/master/router.py` · `master/critic/router.py` 에서 옮겼다). 거기서 들일 수 있는 것은
#: `tests/architecture/test_master_layers.py::test_http_entries_import_only_what_they_may` 가 잰다.
_MASTER_HTTP = ("api/master/", "api/critic/")


def _api_master_refs() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in _files("api"):
        if _rel(path).startswith(_MASTER_HTTP):
            continue
        for line, shape, names in module_refs(path.read_text(encoding="utf-8")):
            reached = [n for n in names if is_under(n, "app.master")]
            if reached:
                out.setdefault(_rel(path), []).append(f"{line}: {shape} → {reached[-1]}")
    return out


def test_screen_takes_only_readmodel_and_domain_from_master():
    """화면이 마스터에서 들이는 것은 readmodel(조회 결과)과 domain(순수 판정)뿐이다."""
    refs = _api_master_refs()
    wrong = {
        path: [ref for ref in lines if not ref.split(" → ")[1].startswith(API_MAY_TAKE_FROM_MASTER)]
        for path, lines in refs.items()
    }

    assert {path: lines for path, lines in wrong.items() if lines} == {}
    #  ★ 무엇을 봤는지 — 지금 마스터를 부르는 화면은 대시보드 · 매입 탭 · 콘솔 실행 목록 셋이다.
    #    (2026-09-29 재구성 BL-014: 콘솔 실행 목록과 매입 탭 조회가 `finance.db` 직접 SQL 에서
    #    마스터 readmodel 로 옮겨 왔다.)
    #    (2026-09-30 재구성 BL-019: 탭 `query.py` 는 `presenter.py` 가 되었다. 마스터 · Critic
    #    HTTP 입구는 화면이 아니라 이 검사에서 뺀다 — 빠진 것이 실제로 있는지도 잰다.)
    assert set(refs) == {
        "api/console/routes.py",
        "api/dashboard/presenter.py",
        "api/purchase/presenter.py",
    }, refs
    assert {_rel(path) for path in _files("api") if _rel(path).startswith(_MASTER_HTTP)} >= {
        "api/master/flows.py",
        "api/critic/verdicts.py",
    }


def test_screen_check_catches_planted_repository_imports():
    planted = (
        "from app.master.purchase_record_repository import recorded_totals_by_plan\n"
        "from app.master import purchase_record_repository\n"
        "from app.master.readmodel.purchase_record import RecordedTotals\n"
    )
    reached = [
        names[-1]
        for _, _, names in module_refs(planted)
        if not names[-1].startswith(API_MAY_TAKE_FROM_MASTER)
    ]

    assert reached == [
        "app.master.purchase_record_repository.recorded_totals_by_plan",
        "app.master.purchase_record_repository",
    ]


# ── 새 domain 은 입력 → 출력만 ───────────────────────────────────────────


def _domain_violations(source: str) -> list[str]:
    bad: list[str] = []
    for line, shape, names in module_refs(source):
        top = names[0].split(".")[0]
        if top == "app":
            module = names[0]
            parts = module.split(".")
            taken = {name.rsplit(".", 1)[-1] for name in names[1:]}
            ok = (
                module.startswith(_DOMAIN_MAY_TAKE)
                or "schemas" in parts
                or "domain" in parts
                or (shape.startswith("from ") and bool(taken)
                    and taken <= _DOMAIN_MAY_TAKE_NAMES.get(module, frozenset()))
            )
        else:
            ok = top in sys.stdlib_module_names and top not in _SIDE_EFFECT_STDLIB
        if not ok:
            bad.append(f"{line}: {shape}")
    return bad


@pytest.mark.parametrize("sub", DOMAIN_DIRS)
def test_domain_does_not_import_db_http_screen_or_read_layers(sub):
    files = _files(sub)
    assert files, f"{sub} 에서 파일을 못 읽었다"
    found = {
        _rel(path): hits
        for path in files
        if (hits := _domain_violations(path.read_text(encoding="utf-8")))
    }

    assert found == {}, found


def test_domain_check_catches_planted_side_effect_imports():
    planted = (
        "import psycopg\n"
        "from fastapi import HTTPException\n"
        "from app.api.logistics.query import _SEVERITY\n"
        "from app.logistics.historical_repository import reservation_state_at\n"
        "from app.core import db\n"
        "from urllib.request import urlopen\n"
        "from app.logistics.console_service import get_inbound_console\n"
    )

    assert len(_domain_violations(planted)) == 7, _domain_violations(planted)
    # ★ 2026-09-30 재구성 BL-015: 계층 폴더로 갈린 뒤에도 부작용 계층은 그대로 잡는다.
    planted_layers = (
        "from app.logistics.repository.ledger import select_lot\n"
        "from app.logistics.readmodel.console import read_console_page\n"
        "from app.logistics.service.outbound import reserve_stock\n"
        "from app.logistics.repository import rows\n"
        "from app.core.clock import seoul_now\n"
        "from app.core.clock import SEOUL, seoul_now\n"
        "import app.core.clock\n"
    )
    assert len(_domain_violations(planted_layers)) == 7, _domain_violations(planted_layers)
    assert _domain_violations(
        "from __future__ import annotations\nfrom datetime import date\n"
        "from app.logistics.schemas import ConsoleReservation\n"
        "from app.logistics.monitoring.schemas import ExceptionRow\n"
        "from app.contracts.core import ITEMS\n"
        "from app.logistics.schemas.snapshot import InventoryLotSnapshot\n"
        "from app.logistics.domain.tools import build_lot_constraints\n"
        "from app.core.clock import SEOUL\n"
    ) == []


def test_loading_logistics_domain_loads_no_db_or_http_module():
    """실행 중 확인 — 타입은 주석에만 쓰므로 `app.logistics.schemas`(→ 출고 · DB)가 안 딸려 온다.

    ⚠️ 마스터 domain 은 여기서 재지 않는다. `app.master` 패키지의 `__init__` 이 흐름 전체를
       불러오므로(설계서 대응표 마스터 `__init__.py` 행) 패키지를 여는 순간 DB 모듈이 올라온다.
       마스터 domain 파일 자체의 import 는 위 AST 검사가 본다.
    """
    code = (
        "import sys\n"
        "import app.logistics.domain.console_rules\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in "
        "('psycopg', 'psycopg_pool', 'fastapi', 'starlette') or m.startswith('app.api') "
        "or m in ('app.core.db', 'app.logistics.db', 'app.logistics.schemas'))\n"
        "print(loaded)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=_APP.parent,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "[]", result.stdout
