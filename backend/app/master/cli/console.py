"""CLI 공용 콘솔 설정 — Windows 콘솔에서 한글 출력이 깨지지 않게 표준 출력을 UTF-8 로 맞춘다.

★ 2026-09-30 재구성 BL-018: `master/backtest_runner.py` 에서 옮겼다 — `use_utf8_output`.
"""

from __future__ import annotations

import sys


def use_utf8_output() -> None:
    """요약을 찍다 죽지 않게 출력 스트림을 UTF-8 로 맞춘다.

    🔴 **걷기를 다 마치고 `print` 에서 죽었다** (2026-09-09 실측).

    ```text
    UnicodeEncodeError: 'cp949' codec can't encode character '\\u2014'
      File "app/master/backtest_runner.py", line 401, in main
        print(format_summary(result))
    ```

      `format_summary` 는 한국어와 `—` 로 적는다. 윈도우 기본 인코딩이 cp949 라
      그 한 글자에서 터졌고, **결과는 다 계산해 놓고 성적표만 잃었다** — 다시
      보려면 걷기를 통째로 또 돌려야 한다.

    ★ **요약 문장을 ASCII 로 낮추지 않는다.** 사람이 읽으라고 쓴 한국어이고,
      바꿔야 할 것은 문장이 아니라 그 문장을 내보내는 통로다.

    ⚠️ **여기서만 바꾼다 — 라이브러리 코드가 아니라 진입점이다.** `walk()` 나
      `format_summary()` 가 프로세스 전역 스트림을 건드리면, 그것을 부르는 쪽의
      출력 설정까지 이 모듈이 정하는 셈이 된다.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # ★ 감싸인 스트림(파이프 대역·캡처)이면 그쪽 규칙을 따른다. 여기서
            #   억지로 바꾸려다 진입점이 터지면 고치려던 것과 같은 일이 난다.
            continue
        reconfigure(encoding="utf-8", errors="backslashreplace")
