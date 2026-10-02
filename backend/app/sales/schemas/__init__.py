"""판매 입출력·내부 모델 — 다른 판매 계층을 import 하지 않는다.

파일 이름은 기능을 따른다. 같은 이름의 `domain/` · `service/` · `repository/` · `readmodel/`
파일이 그 기능의 판단 · 업무 순서 · SQL · 조회 조립을 맡는다.

```text
proposal         판매 제안 입력 · 안 · 회신 (마스터 봉투 payload 의 정본 모양)
proposal_state   판매 제안 그래프가 노드 사이로 나르는 상태
strategy         세 전략의 자세 어휘와 계획 · 자세를 고르는 사실
sale_ledger      승인된 안을 판매 원장에 적는 입력 · 계획 · 결과 · 충돌
runs             실행이력 어휘와 저장 · 조회 모양
partners         거래처 기본정보 입력 · 저장 모양 · 업무 예외
dashboard        판매 현황 응답
console_*        운영 콘솔 조회 응답 (같은 이름의 readmodel 이 채운다)
```

이 패키지는 이름을 다시 내보내지 않는다 — 쓰는 쪽이 모듈을 직접 가리킨다.
"""
