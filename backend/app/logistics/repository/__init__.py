"""물류 SQL — 받은 연결로 실행만 한다. 연결을 빌리지도, commit · rollback 하지도 않는다.

판단 · 조립 · 순서를 부르지 않는다(`domain` · `service` · `readmodel` 을 import 하지 않는다). 행
0 · 1 · 2건 이상을 가르고 행을 계약 타입으로 읽는 것까지가 이 계층이다. 스키마 이름과 행 읽기
도우미는 `rows.py`, 쓰기 전역 잠금과 그 순서 표는 `locks.py`.
"""
