"""새 DB 적용 목록(`database/new_database_order.txt`)이 스키마 파일 전부를 **참조 순서대로** 담는가.

2026-09-30 BL-021 — 스키마를 부서 폴더 · 객체(표 · 뷰)별 파일로 나누고 파일 이름의 숫자 접두어를
뗐다. 순서는 이제 파일 이름이 아니라 이 목록이 정한다. 목록이 낡으면 새 DB 가 세워지다 멈추거나
(참조하는 표가 아직 없다) 파일 하나가 조용히 빠진다 — 여기서 잰다. SQL 텍스트만 본다(DB 불필요).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DATABASE = REPO / "database"
ORDER_FILE = DATABASE / "new_database_order.txt"

_CREATES = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TABLE|VIEW|SEQUENCE|FUNCTION)\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"haetdeul\.(\w+)",
    re.IGNORECASE,
)
_REFERENCES = re.compile(r"haetdeul\.(\w+)")


def _listed() -> list[str]:
    return [
        line.strip()
        for line in ORDER_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _code_only(text: str) -> str:
    """주석 · 문자열 · psql 명령 줄을 걷은 실행 코드. `$태그$` 본문(함수 · DO)은 코드로 남긴다."""
    out: list[str] = []
    i, n = 0, len(text)
    dollar = ""
    while i < n:
        if dollar:
            end = text.find(dollar, i)
            end = n if end == -1 else end
            out.append(text[i:end])
            i, dollar = end + len(dollar), ""
            continue
        if (i == 0 or text[i - 1] == "\n") and text[i:].lstrip(" \t").startswith("\\"):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text.startswith("--", i):
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if text[i] == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and text.startswith("''", j):
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            out.append("''")
            i = j + 1
            continue
        tag = re.match(r"\$[A-Za-z_]*\$", text[i:])
        if tag:
            dollar = tag.group(0)
            i += len(dollar)
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _order_violations(paths: list[Path]) -> list[str]:
    """앞선 파일이 아직 만들지 않은 저장소 객체를 가리키는 파일."""
    codes = {path: _code_only(path.read_text(encoding="utf-8")) for path in paths}
    maker: dict[str, Path] = {}
    for path, code in codes.items():
        for name in _CREATES.findall(code):
            maker.setdefault(name, path)
    made: set[str] = set()
    problems = []
    for path in paths:
        code = codes[path]
        own = set(_CREATES.findall(code))
        for name in sorted(set(_REFERENCES.findall(code)) - own):
            if name in maker and name not in made:
                problems.append(f"{path.relative_to(REPO).as_posix()} → {name}")
        made |= own
    return problems


def test_every_schema_file_is_listed_exactly_once() -> None:
    listed = _listed()
    schema_files = sorted(
        p.relative_to(REPO).as_posix() for p in (DATABASE / "schema").rglob("*.sql")
    )

    assert len(listed) == len(set(listed)), "적용 목록에 같은 파일이 두 번 있다"
    assert sorted(entry for entry in listed if entry.startswith("database/schema/")) == schema_files
    assert all((REPO / entry).is_file() for entry in listed), "적용 목록에 없는 파일이 있다"


def test_the_list_starts_with_the_schema_and_never_loads_seed_data() -> None:
    listed = _listed()

    assert listed[0] == "database/schema/common/haetdeul_schema.sql"
    assert not [entry for entry in listed if entry.startswith("database/seed/")], (
        "seed 는 새 DB 목록에 넣지 않는다 — 사용자가 확인하고 직접 넣는다"
    )


def test_the_list_loads_only_schema_files() -> None:
    """각 schema 파일이 새 DB 의 최종 정의다 — migrations/ 는 이미 쓰는 DB 를 갱신할 때만 쓴다.

    2026-09-30 BL-021 보완 전에는 목록 끝의 migrations/ 8개가 앞선 schema 파일을 뒤에서 고쳐서,
    표 파일 하나만 읽어서는 새 DB 의 최종 모양을 알 수 없었다. 그 효과는 해당 schema 파일에 담는다.
    """
    outside = [entry for entry in _listed() if not entry.startswith("database/schema/")]

    assert not outside, f"새 DB 목록에 schema/ 밖의 파일이 있다: {outside}"


def test_schema_file_names_carry_no_numeric_order_prefix() -> None:
    prefixed = [
        p.relative_to(REPO).as_posix()
        for p in (DATABASE / "schema").rglob("*.sql")
        if re.match(r"\d+_", p.name)
    ]

    assert not prefixed, f"순서는 목록이 정한다 — 파일 이름에 숫자 접두어를 달지 않는다: {prefixed}"


def test_every_file_comes_after_the_objects_it_points_to() -> None:
    assert _order_violations([REPO / entry for entry in _listed()]) == []


def test_the_order_check_catches_a_file_listed_before_its_dependency() -> None:
    """⚠️ 위 검사가 공허하지 않은지 — 표와 그 표를 가리키는 표의 순서를 뒤집으면 잡혀야 한다."""
    listed = [REPO / entry for entry in _listed()]
    items = next(p for p in listed if p.name == "items.sql")
    sale_items = next(p for p in listed if p.name == "sale_items.sql")
    swapped = [p for p in listed if p != items]
    swapped.insert(swapped.index(sale_items) + 1, items)

    assert any(problem.endswith("→ items") for problem in _order_violations(swapped))
