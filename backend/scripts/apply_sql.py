"""저장소의 `.sql` 파일 하나를 **있는 그대로** 실행한다.

DDL 을 손으로 옮겨 적으면 **파일과 실제로 돈 것이 갈리고**, 그때 어느 쪽이 맞는지
답할 방법이 없다. 그래서 파일을 읽어 그대로 보낸다 — 이 스크립트는 SQL 을
**한 글자도 만들지 않는다.**

```bash
# 무엇이 돌지 먼저 본다 (DB 에 붙지 않는다)
.venv/Scripts/python.exe scripts/apply_sql.py database/migrations/파일.sql --dry-run

# 실제로 적용한다
.venv/Scripts/python.exe scripts/apply_sql.py database/migrations/파일.sql --apply

# 빈 DB 를 세운다 — database/new_database_order.txt 순서대로 (먼저 목록만 본다)
.venv/Scripts/python.exe scripts/apply_sql.py --new-database
.venv/Scripts/python.exe scripts/apply_sql.py --new-database --apply
```

🔴 **`--apply` 를 안 주면 아무것도 안 한다.** 기본이 「보여주기」인 이유는, 공유 DB 에
   스키마를 바꾸는 일은 되돌리기가 어렵고 *"돌릴 생각은 없었다"* 가 한 번이면
   충분하기 때문이다.

★ 접속 정보는 `backend/.env` 에서 읽는다. 여기에 호스트도 비밀번호도 안 적는다.

★ **보내는 길이 둘이다** (2026-09-30).
  · 보통 파일은 psycopg 로 본문 전체를 한 번에 보낸다(종전 그대로 — psql 이 없어도 된다).
  · 줄 머리가 역슬래시인 **psql 명령**(`\\restrict` · `\\unrestrict` · `\\i`)이 든 파일은
    서버가 SQL 로 받지 못한다. 그런 파일만 `psql -v ON_ERROR_STOP=1 -f` 로 보내고,
    psql 이 실패하면 이 스크립트도 그 종료 코드로 실패한다. psql 조건(버전 · PATH)은
    `database/README.md` §2 에 있다. 명령 줄을 지우거나 고쳐서 보내지 않는다.
  · 지금 그런 파일은 이미 쓰는 DB 용 `migrations/master/master_agent_runs_migration.sql`(`\\i`)
    하나다. 새 DB 목록(`--new-database`)에는 psql 명령 줄이 든 파일이 없어 psql 없이 선다
    (2026-09-30 BL-021 보완).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / ".env"
_REPO = Path(__file__).resolve().parent.parent.parent
_NEW_DATABASE_ORDER = _REPO / "database" / "new_database_order.txt"


def _require_db_env() -> None:
    load_dotenv(_ENV)
    빠진것 = [이름 for 이름 in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER") if not os.getenv(이름)]
    if 빠진것:
        raise SystemExit(f"🔴 {_ENV} 에 없는 값: {', '.join(빠진것)}")


def _dsn() -> str:
    _require_db_env()
    return (
        f"host={os.getenv('DB_HOST')} port={os.getenv('DB_PORT')}"
        f" dbname={os.getenv('DB_NAME')} user={os.getenv('DB_USER')}"
        f" password={os.getenv('DB_PASSWORD')}"
    )


def _psql_command_lines(text: str) -> list[tuple[int, str]]:
    """줄 머리가 역슬래시인 줄 — psql 명령이다. (행 번호, 명령 이름) 목록."""
    return [
        (number, line.split()[0])
        for number, line in enumerate(text.splitlines(), 1)
        if line.lstrip().startswith("\\")
    ]


def _apply_with_psql(path: Path) -> int:
    """psql 로 파일을 그대로 돌린다. 오류에서 멈추고(`ON_ERROR_STOP`) 그 종료 코드를 돌려준다.

    ★ 저장소 루트에서 돌린다 — 파일 안의 `\\i database/…` 가 그 자리를 기준으로 적혔다.
    ★ 비밀번호는 명령줄이 아니라 환경변수(`PGPASSWORD`)로 넘긴다. 파일은 UTF-8 이라
      클라이언트 인코딩을 박아 둔다(Windows 기본 코드 페이지로 읽으면 한글이 깨진다).
    """
    psql = shutil.which("psql")
    if psql is None:
        raise SystemExit(
            "🔴 psql 을 찾지 못했다 — psql 명령 줄이 든 파일은 psql 로만 돈다"
            " (PATH 의 psql 17.6 이상 · database/README.md §1)"
        )
    _require_db_env()
    env = dict(os.environ)
    env.update(
        PGHOST=os.environ["DB_HOST"],
        PGPORT=os.environ["DB_PORT"],
        PGDATABASE=os.environ["DB_NAME"],
        PGUSER=os.environ["DB_USER"],
        PGCLIENTENCODING="UTF8",
    )
    password = os.getenv("DB_PASSWORD")
    if password:
        env["PGPASSWORD"] = password
    command = [psql, "-X", "-w", "-v", "ON_ERROR_STOP=1", "-f", str(path)]
    result = subprocess.run(command, cwd=_REPO, env=env, check=False)
    if result.returncode != 0:
        print(
            f"🔴 psql 이 실패했다 — 종료 코드 {result.returncode}."
            " 멈춘 문장 앞은 이미 적용됐을 수 있다."
        )
        return result.returncode
    print("🟢 끝났다.")
    return 0


def _apply_with_psycopg(text: str) -> int:
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cursor:
        # ★ 파일이 `BEGIN;` / `COMMIT;` 을 직접 들고 있을 수 있으므로 autocommit 으로
        #   보낸다 — 여기서 트랜잭션을 또 열면 파일의 경계와 두 벌이 된다.
        cursor.execute(text)
    print("🟢 끝났다.")
    return 0


def _read_new_database_order(order_file: Path) -> list[Path]:
    """새 DB 적용 목록 — 한 줄에 파일 하나(저장소 루트 기준), `#` 줄과 빈 줄은 건너뛴다."""
    if not order_file.is_file():
        raise SystemExit(f"🔴 적용 목록이 없다: {order_file}")
    paths: list[Path] = []
    for line in order_file.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        path = (_REPO / entry).resolve()
        if not path.is_file():
            raise SystemExit(f"🔴 적용 목록의 파일이 없다: {entry}")
        if path in paths:
            raise SystemExit(f"🔴 적용 목록에 같은 파일이 두 번 있다: {entry}")
        paths.append(path)
    return paths


def _haetdeul_object_count() -> int:
    with psycopg.connect(_dsn(), autocommit=True) as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'haetdeul'"
        )
        return int(cursor.fetchone()[0])


def _new_database(apply: bool) -> int:
    """`database/new_database_order.txt` 순서대로 **빈 DB** 를 세운다.

    ★ 순서는 목록이 정한다 — 파일 이름 정렬에 기대지 않는다.
    🔴 대상 DB 의 `haetdeul` 에 객체가 하나라도 있으면 멈춘다. 이미 쓰는 DB 는 §2 의
       이관 판을 한 파일씩 쓴다(`database/README.md`).
    """
    paths = _read_new_database_order(_NEW_DATABASE_ORDER)
    print(f"목록   {_NEW_DATABASE_ORDER} · 파일 {len(paths)}개")
    for number, path in enumerate(paths, 1):
        runner = "psql" if _psql_command_lines(path.read_text(encoding="utf-8")) else "psycopg"
        print(f"{number:3}  {runner:7}  {path.relative_to(_REPO).as_posix()}")
    if not apply:
        print("🟡 안 돌렸다. 실제로 세우려면 --apply 를 준다.")
        return 0

    load_dotenv(_ENV)
    target = f"{os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}"
    print(f"🔴 새 DB 를 세운다 — {target}")
    existing = _haetdeul_object_count()
    if existing:
        raise SystemExit(
            f"🔴 빈 DB 가 아니다 — haetdeul 에 객체가 {existing}개 있다."
            " 새 DB 구축은 빈 DB 에만 한다."
        )
    for number, path in enumerate(paths, 1):
        text = path.read_text(encoding="utf-8")
        print(f"── {number}/{len(paths)} {path.relative_to(_REPO).as_posix()}")
        code = _apply_with_psql(path) if _psql_command_lines(text) else _apply_with_psycopg(text)
        if code != 0:
            print(f"🔴 {number}번째 파일에서 멈췄다 — 뒤 파일은 적용하지 않았다.")
            return code
    print(f"🟢 새 DB 를 세웠다 — 파일 {len(paths)}개.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="`.sql` 파일 하나를 있는 그대로 실행한다")
    parser.add_argument(
        "sql_file", nargs="?", help="실행할 파일 (저장소 루트 기준 또는 절대 경로)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="🔴 실제로 적용한다. 안 주면 내용만 보여주고 DB 에 안 붙는다",
    )
    parser.add_argument("--dry-run", action="store_true", help="내용만 본다 (기본 동작)")
    parser.add_argument(
        "--new-database",
        action="store_true",
        help="database/new_database_order.txt 순서대로 빈 DB 를 세운다 (파일 인자 대신)",
    )
    args = parser.parse_args()

    if args.new_database:
        if args.sql_file:
            parser.error("--new-database 는 파일 인자와 함께 쓰지 않는다")
        return _new_database(args.apply)
    if not args.sql_file:
        parser.error("실행할 파일을 주거나 --new-database 를 준다")

    경로 = Path(args.sql_file)
    if not 경로.is_absolute():
        경로 = (_REPO / args.sql_file).resolve()
    if not 경로.exists():
        raise SystemExit(f"🔴 파일이 없다: {경로}")

    본문 = 경로.read_text(encoding="utf-8")
    psql_lines = _psql_command_lines(본문)
    print(f"파일   {경로}")
    print(f"길이   {len(본문):,}자 · {본문.count(chr(10)) + 1}줄")
    if psql_lines:
        found = " · ".join(f"{number}행 {name}" for number, name in psql_lines)
        print(f"실행   psql — psql 명령 줄이 있다 ({found})")
    else:
        print("실행   psycopg — 본문 전체를 한 번에")
    print("─" * 70)
    print(본문)
    print("─" * 70)

    if not args.apply:
        print("🟡 안 돌렸다. 실제로 적용하려면 --apply 를 준다.")
        return 0

    load_dotenv(_ENV)
    print(f"🔴 적용한다 — {os.getenv('DB_HOST')}:{os.getenv('DB_PORT')}/{os.getenv('DB_NAME')}")
    if psql_lines:
        return _apply_with_psql(경로)
    return _apply_with_psycopg(본문)


if __name__ == "__main__":
    sys.exit(main())
