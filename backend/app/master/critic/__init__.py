r"""Critic Agent 패키지 — 마스터가 쓰는 검증 Tool.

────────────────────────────────────────────────────────────────────────────
파일을 합치지 않는 이유. 파일이 일곱이라 "좀 합치지" 가 나오는 자리다.

① 일곱 다 쓰이는 경로다.

```text
api/critic/verdicts.py → service.py → critic_v0_4.py → critic.py
                                    → llm/judge.py → llm/runtime.py → llm/schemas.py
                       → schemas.py → llm/schemas.py
```

  판단 경로도 같은 자리로 들어온다 — `service/procurement.py` 의 `MasterVerifier()` →
  `service/verifier.py` → `service.run_critic_procurement`.

② 큰 둘은 «감싸는» 구조다. 합치면 천 줄이 훌쩍 넘는 한 파일이 된다(`critic.py` +
  `critic_v0_4.py`). 그 결정은 두 파일 머리말에 적혀 있고 거기가 주인이다 — 여기서
  되풀이하지 않는다.

③ tests 가 이 패키지를 여러 파일에서 부른다.

```bash
grep -rn "^\s*\(from\|import\) app\.master\.critic" tests/ --include=*.py | wc -l
```

  수는 낡는다. 낡았다고 ①②가 뒤집히지 않으니 위 명령으로 다시 재면 된다.

→ 파일 수를 줄이는 것 자체는 값이 아니다. 여기서 줄어드는 것은 파일 수 하나이고
  늘어나는 것은 한 파일의 줄 수와 그것을 읽는 다음 사람의 품이다.

`critic_v0_4` 라는 이름도 낡은 표시가 아니다. 설계서 v0.4 를 가리키는 좌표라
바꾸면 그 연결이 끊긴다.
"""
