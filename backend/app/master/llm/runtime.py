"""의도 분류 — 프로바이더 · 검증 · 재시도 · fallback.

팀 규약(finance·logistics·orchestrator·critic·purchase 5벌)과 같은 배치다.
프로바이더는 3종이고 `LLM_PROVIDER` 로 고른다. 에이전트 접두사는 `MASTER_`.

★ **검증 체인은 프로바이더 밖에 있다.** 프로바이더는 "문자열을 받아온다"까지만 하고,
  닫힌 열거 대조·숫자 출처 검사·재시도는 `IntentService` 가 소유한다.

★ **API 키는 `.env` 에서만 읽는다.** `LLMSettings` 에 싣지 않는다 — 설정 객체는 로그·
  예외에 실릴 수 있다. 키가 없으면 예외를 던지고 **fallback 으로 간다.**

★ **프로바이더 호출은 `app.core.llm` 이 한다** (2026-09-30 재구성 BL-020). 이 파일에 남은 것은
  마스터의 몫이다 — 지시문 · 응답 스키마 · 검증 체인 · `MASTER_` 설정값과 오류 문장 · 재시도
  규칙(검증 실패만 다시 묻고 전송 실패는 곧바로 되묻기로 간다). 프로바이더는 도메인 타입을
  모르는 형태(`generate(system, user, schema) -> str`) 그대로다.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import ValidationError

from app.contracts.envelope import LLMStatus, agent_allowed_modes
from app.core.llm.providers import (
    GEMINI_BASE_URL,
    anthropic_json,
    chat_messages,
    first_text,
    gemini_json_request,
    gemini_parts,
    gemini_request,
    gemini_strict_schema,
    ollama_chat_request,
    ollama_request,
    ollama_text,
    openai_json,
    require_model,
    send_json,
)
from app.core.llm.runtime import (
    ENV_FILES,
    OLLAMA_BASE_URL,
    float_env,
    gemini_api_key,
    int_env,
    load_env_files,
    read_bool,
    resolve_provider_model,
    run_with_fallback,
    scoped_env,
)
from app.master.llm.schemas import Intent, IntentResult

#: 🔴 **분류가 왜 실패했는지를 남기는 자리다** (2026-09-16). `day_opening_repository`
#:    와 같은 형식이다 — 모듈 이름으로 받아 두고 삼킨 예외의 **종류와 문장**을 적는다.
logger = logging.getLogger(__name__)

_ENV_PREFIX = "MASTER_"

_DEFAULT_MODELS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "gemma3:4b",
    #: 🔴 stable 을 pin 한다 — `latest`·`preview` 같은 자동 갱신 별칭은 출력 성향이
    #: 예고 없이 바뀐다. 물류가 #95 에서 고른 것과 같은 모델이다 (팀 안에서 두 파트가
    #: 다른 모델을 쓰면 "모델이 달라서 그런가" 가 모든 조사에 끼어든다).
    "gemini": "gemini-3.5-flash-lite",
}

#: 발화문에 없던 숫자를 조건에 지어넣는 것을 막는다. 매입 ⑤의 "숫자 금지"와 다르다 —
#: 여기서는 **사용자가 말한 숫자는 허용**하고, 출처 없는 숫자만 거부한다.
_DIGITS = re.compile(r"\d")

#: 🔴 「부서 이름 (agents)」 절의 ml 갈래를 셋으로 늘렸다 (2026-09-16 · ML `#746`).
#: ML 이 질의응답에 **배치/데이터 처리**와 **모델 성능/재학습** 갈래를 더했고, 품목 이름이
#: 없어도 가격 질문에 답하게 됐다. 지시문이 「품목 이름이 ml 을 가른다」로 남아 있으면
#: *"오늘 데이터 처리 잘 됐어?"* · *"모델 성능 어때?"* · *"오늘 가격 알려줘"* 셋이 ml 로
#: 오지 않는다 — **답할 수 있게 된 것을 지시문이 막고 있던 것**이다.
#: 보내는 payload 는 그대로다 (`STATUS_QUERY` · agent `"ml"` · `{"question", "item"}`).
#: 주석이 문자열 안으로 못 들어가서 여기 둔다 — 고친 자리는 아래 「부서 이름」 절이다.
SYSTEM_PROMPT = """당신은 햇들농산 매입 의사결정 시스템의 요청 해석 레이어다.
사용자의 한국어 발화문을 정해진 종류 중 하나로 분류하는 것이 전부다.

절대 규칙:
- 실행하지 않는다. 분류만 한다.
- 지정된 JSON Schema 에 맞는 JSON 만 출력한다. 설명 문장을 덧붙이지 않는다.
- 목록에 없는 값을 만들지 않는다.
- **어느 종류인지** 확실하지 않으면 UNKNOWN · LOW 로 둔다.
  모르겠다고 답하는 것이 틀리게 분류하는 것보다 낫다.

action 종류와 예시:

PROCUREMENT_RUN — 살 안을 **만들어 달라**
  "오늘 배추 얼마나 사야 해?"   "무 매입안 뽑아줘"   "오늘 뭘 사면 좋을까"
  "배추 매입 계획 만들어줘"      "얼마나 들여와야 하지?"

STATUS_QUERY — 매입·재고·ML 등 기존 부서의 **지금 상태만** 묻는다 (안을 만들지 않는다)
  "창고에 얼마나 남았어?"   "재고 어때?"   "지금 창고 여유 있나?"
  ★ 회사의 현금·자금·미수·미지급·비용·여신은 STATUS_QUERY가 아니라 아래 FINANCE_* DOMAIN_ACTION이다.
  ★ 재고·물류도 **지금 상태를 묻는 말**은 여기다. 「보고서 · 리포트 · PDF」를 달라고 할 때만
    아래 LOGISTICS_REPORT_GENERATE 다.

RERUN_WITH_CONDITION — **조건을 붙여 다시** 만들어 달라
  "예산 2천만원으로 낮춰서 다시"   "좀 적게 사는 걸로 다시 해줘"

SELECT_SCENARIO — **이미 나와 있는 안 중 하나를 고른다**
  "기본안으로 진행해"   "보수안 선택할게"   "두 번째 걸로 해줘"

DOMAIN_ACTION — 재무·판매·재고물류·거래처의 구체적인 조회·등록·변경·보고서 요청
  재무: 현금/미수/미지급/현금흐름 조회, 자금 입출금, 여신 조회·변경,
        수금, 비용 조회·등록·지급·취소, 재무 보고서
  판매: 판매 후보 생성, 오늘 판매안, 오늘 확정 판매, 판매 보고서
  재고물류: 재고·물류 보고서
  거래처: 목록, 등록, 상세, 수정
  ★ domain_action은 허용 목록에서 하나만 고른다.
  ★ slots는 사용자가 실제로 말한 표현만 옮긴다. 금액·수량·날짜를 계산하지 않는다.
  ★ partner_id/expense_id/receivable_id를 발화에 없는데 지어내지 않는다.

  자연어 예시 (의미가 분명하면 띄어쓰기·조사·일반 오타가 있어도 같은 action):
  ★ Finance 조회는 회사의 저장된 재무 read model을 읽는다. 부서 상태 조회로 보내지 않는다.
  FINANCE_SUMMARY_GET: "지금 돈 얼마나 있어?" · "우리 자금 괜찮아?" · "현재 현금 얼마야?"
                       "자금 상황 보여줘" · "재무 상태 어때?" · "돈 얼마나 남았어?"
                       "현재 자금 현황 알려줘"
  FINANCE_CREDIT_LIMIT_GET: "<거래처명> 여신한도 얼마야?" · "<거래처명> 한도 보여줘"
                           "<거래처명> 신용한도 알려줘" · "<거래처명> 얼마까지 외상 가능해?"
                           "<거래처명> 가용 여신 얼마 남았어?"
  FINANCE_CREDIT_LIMIT_UPSERT: "<거래처명> 여신한도 3000만원으로 바꿔줘"
                              "<거래처명> 한도 5천만원으로 올려줘"
                              "<거래처명> 신용한도 1000만원으로 설정해줘"
                              "<거래처명> 여신한도 0원으로 변경해줘"
  FINANCE_RECEIVABLES_GET: "받을 돈 보여줘" · "미수금얼마야" · "돈 못 받은 거 정리해줘"
  FINANCE_PAYABLES_GET: "이번주 나갈돈" · "지급해야 할 돈 보여줘" · "미지급금 얼마야?"
                        "오늘 줘야 하는 돈 있어?" · "이번 주 결제 예정 보여줘"
                        "연체된 지급 건 있어?" · "앞으로 나갈 돈 정리해줘"
  FINANCE_CASHFLOW_GET: "이번 주 현금 흐름 보여줘" · "돈 들어오고 나간 거 보여줘"
                        "최근 자금 흐름 알려줘" · "이번달 현금흐름" · "최근 7일 자금 흐름 보여줘"
  FINANCE_EXPENSE_LIST: "비용 내역 보여줘" · "이번달 쓴돈 보여줘" · "최근 지출 내역"
                        "등록된 비용 뭐 있어?" · "아직 안 낸 비용 있어?"
                        "미지급 비용 보여줘" · "오늘 비용 내역"
  FINANCE_EXPENSE_CREATE: "임차료 200만원 비용 등록해줘" · "오늘 운영비 50만원 잡아줘"
                         "비용 하나 등록할게" · "300만원 지출 예정으로 넣어줘"
  FINANCE_EXPENSE_SETTLE: "<expense_id> 지급 처리해줘" · "<expense_id> 결제 완료로 처리해줘"
  FINANCE_EXPENSE_CANCEL: "<expense_id> 비용 취소해줘" · "<expense_id> 지출 건 없던 걸로 해줘"
  FINANCE_COLLECTION_CREATE: "<거래처명>에서 돈 들어왔어" · "<거래처명> 미수금 수금 처리해줘"
                             "<거래처명> 받을 돈 중 100만원 들어왔어" · "이 미수금 전액 수금됐어"
  FINANCE_CASH_ADJUSTMENT_CREATE: "300만원 입금 기록해줘" · "운영자금 300만원 들어왔어"
                                  "50만원 출금 처리해줘" · "오늘 100만원 빠져나갔어"
  FINANCE_REPORT_GENERATE: "재무리포트 보여줘" · "이번주 돈 보고서 뽑아줘"
                           · "최근 30일 재무 보고서" · "1년 재무 보고서"
                           · "6월 1일부터 6월 10일까지 재무 보고서" · "재무 보거서"
  SALES_PROPOSALS_TODAY: "오늘판매안" · "오늘 팔 수 있는 거" · "판매 추천안 보여줘"
  SALES_CONFIRMED_TODAY: "오늘 진짜 팔린 거" · "확정된 판매건" · "실제로 확정된 것만"
  SALES_REPORT_GENERATE: "판매 리폿" · "이번 주 영업 보고서" · "판매 PDF 만들어줘"
  LOGISTICS_REPORT_GENERATE: "재고 보고서 보여줘" · "물류 보고서 보여줘" · "재고 물류 리포트"
                             "이번 주 재고 보고서" · "이번 달 물류 보고서"
                             "재고 물류 PDF 만들어줘" · "창고 운영 보고서 뽑아줘"
  ★ 재고·물류는 **보고서를 달라고 할 때만** 이것이다. "창고에 얼마나 남았어?" ·
    "재고 어때?" · "지금 창고 여유 있나?" 처럼 지금 상태를 묻는 말은 STATUS_QUERY 다.
  PARTNER_LIST: "거래처목록" · "우리 거래처 뭐 있어" · "고객사 목록 보여줘"
  PARTNER_DETAIL_GET: "<거래처명> 정보" · "<거래처명> 결제조건 알려줘"
  ★ "보고서 만들어줘"처럼 재무/판매/재고물류 대상을 못 정하면 UNKNOWN · LOW다.

UNKNOWN — 위 어디에도 속하지 않거나 무엇을 원하는지 알 수 없다
  "그거 있잖아 그거"   "음..."
  ★ **이 시스템에 답할 자리가 없는 것도 UNKNOWN 이다.** 가까운 부서로 돌리지 마라.
    가까운 부서를 넣으면 사용자는 **물어본 것과 상관없는 숫자**를 받는다.

★ **만들어 달라**와 **고른다**를 구분하라. "안" 이라는 글자로 가르지 마라.
  "매입안 뽑아줘 · 만들어줘 · 얼마나 사야 해"  → 만들어 달라  → PROCUREMENT_RUN
  "기본안으로 · 보수안으로 · 두 번째 걸로"      → 고른다        → SELECT_SCENARIO

★ SELECT_SCENARIO 로 고르면 scenario_label 을 **반드시 채운다** — 사용자가 부른 이름 그대로.
  "기본안으로 진행해"  → scenario_label: "기본"
  "보수안 선택할게"    → scenario_label: "보수"
  "공격안으로 가자"    → scenario_label: "공격"

부서 이름 (agents) — STATUS_QUERY 일 때만 채운다:

  finance     자금 · 현금 · 잔고 · 돈 · 예산 · 지급 · 결제 · 대금 · 자금 사정
  inventory   재고 · 창고 · 보관 · 입고 · 출고 · 용량 · 여유 · 신선도 · 남은 양
  purchase    매입 진행 상황 · 지금 만들어 둔 안
  sales       판매 진행 상황 · 지금 만들어 둔 판매안
  ml          ① 가격 · 시세 · 단가 · 경락가 · 중도매가 · 소매가 · 예측 · 전망
                 **품목 이름이 없어도 ml 이다.** 품목이 안 나왔으면 item 을 비운다.
              ② 배치 · 데이터 처리 · 수집 · 점검 · 점검 보고서 · 오늘 처리 잘 됐나
              ③ 모델 성능 · 정확도 · 오차 · 재학습 · 모델 업데이트 · 현재 모델

★ finance 와 ml 의 경계는 **「회사 돈」인가 「품목 가격」인가**로 가른다.
  "이번 주 대금 얼마 나가?"   → finance   (대금 · 지급 · 결제 = 회사 돈이다)
  "배추 얼마야?"              → ml        (품목 가격이다)
  "오늘 가격 알려줘"          → ml        (품목이 없어도 ml · item 은 비운다)
  "오늘 데이터 처리 잘 됐어?"  → ml        (② 배치다)
  "모델 성능 어때?"           → ml        (③ 성능이다)
★ 갈래가 섞여도 **ml 하나로** 보낸다 ("5일 뒤 배추 경락가랑 배치 상태" → ml 하나).

★ 부서가 여럿이면 여럿을 넣는다 ("자금이랑 창고 둘 다" → finance, inventory).
★ **어느 부서인지** 애매한 것은 UNKNOWN 이 아니다 — 가까운 부서를 넣고 confidence 를
  낮춘다. UNKNOWN 은 **어느 종류인지** 모를 때만 쓴다.
★ **어느 품목인지** 모르는 것도 UNKNOWN 이 아니다. 품목은 비우고 종류는 그대로 둔다.
  "오늘 뭘 사면 좋을까"  → PROCUREMENT_RUN · item: null   (UNKNOWN 이 아니다)
  무엇을 해 달라는지가 분명하면 세부가 비어도 그 종류로 분류한다.

★ **제외 품목** — 피마늘 · 건고추는 이 프로젝트의 대상 품목이 아니다.
  이 둘**만** 나온 질문은 조회든 매입이든 판매든 **UNKNOWN 이다.** 부서를 부르지 마라.
  "피마늘 재고 얼마나 남았어?"  → UNKNOWN   (부서를 부르지 않는다)
  "건고추 재고 알려줘"          → UNKNOWN   (부서를 부르지 않는다)
  "피마늘 얼마나 사야 해?"      → UNKNOWN   (매입도 마찬가지다)
★ 정상 품목(배추·무·양파)이 **함께** 나오면 종전대로 분류한다.
  제외 품목이 섞였다고 질문 전체를 UNKNOWN 으로 접지 마라 — 정상 품목의 답까지 막힌다.
  "배추랑 피마늘 재고 알려줘"   → STATUS_QUERY · inventory · item: "배추"

나머지 필드:
- item 은 배추·무·양파 중 **발화문에 나온 것을 그대로** 옮긴다.
  "오늘 배추 얼마나 사야 해?" → item: "배추"
  "무 매입안 뽑아줘"          → item: "무"
  "오늘 뭘 사면 좋을까"        → item: null   (품목이 안 나왔다)
  발화문에 없으면 null 이다. **추측해서 채우지 않는다.**
- scenario_label 은 SELECT_SCENARIO 일 때만. 사용자가 부른 이름을 그대로 옮긴다.
- condition 은 RERUN_WITH_CONDITION 일 때만. **사용자의 말 그대로** 옮긴다.
  발화문에 없는 숫자를 만들지 않는다.
- domain_action · slots 는 DOMAIN_ACTION 일 때만 채운다.
  slots의 문자열은 사용자가 말한 표현을 그대로 둔다. "3천만원"을 "30000000"으로 바꾸지 않는다.
- confidence 는 분류가 얼마나 확실한지다. 발화문이 모호하면 낮춘다."""


@dataclass(frozen=True)
class LLMSettings:
    """설정. **API 키를 담지 않는다.**"""

    enabled: bool
    provider: str
    model: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    max_output_tokens: int
    effort: str | None


class TextProvider(Protocol):
    """문자열을 받아오는 것까지가 프로바이더의 일이다.

    ★ **도메인 타입을 모른다.** 매입 런타임의 프로바이더는 `SanitizedLLMContext` 를
      받는데, 그러면 공용 층으로 들어낼 수 없다. 여기는 문자열 셋만 받는다.
    """

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> str: ...


def get_llm_settings() -> LLMSettings:
    """`MASTER_` → 공용 → 기본값. `.env` 는 부를 때마다 적재한다(이미 있는 값은 덮지 않는다).

    🔴 **모델은 프로바이더에 종속된 값이다.** 마스터가 전역과 다른 프로바이더를 쓸 때 전역
       `LLM_MODEL`(재무 · Critic 이 같이 보는 `gemma3:4b`)을 상속하면 Gemini 에 없는 모델을
       요청해 404 가 난다 — 그 경우에만 전역 모델을 건너뛴다(`resolve_provider_model` ·
       물류 · Critic 과 같은 규칙).
    """
    load_env_files(ENV_FILES)
    provider, model = resolve_provider_model(
        _ENV_PREFIX, default_provider="anthropic", default_models=_DEFAULT_MODELS
    )
    return LLMSettings(
        enabled=read_bool("LLM_ENABLED", prefix=_ENV_PREFIX, default=True),
        provider=provider,
        model=model.strip(),
        base_url=scoped_env(_ENV_PREFIX, "LLM_BASE_URL", OLLAMA_BASE_URL).rstrip("/"),
        timeout_seconds=float_env(_ENV_PREFIX, "LLM_TIMEOUT_SECONDS", "30", minimum=0.1),
        max_retries=min(1, int_env(_ENV_PREFIX, "LLM_MAX_RETRIES", "1", minimum=0)),
        max_output_tokens=int_env(_ENV_PREFIX, "LLM_MAX_OUTPUT_TOKENS", "1024", minimum=256),
        effort=(scoped_env(_ENV_PREFIX, "LLM_EFFORT", "").strip() or None),
    )


def _intent_schema() -> dict[str, Any]:
    """구조화 출력에 넘길 JSON Schema.

    🔴 **기본값이 있는 칸을 `required` 로 올린다 — 안 그러면 모델이 그 칸을 안 쓴다.**

    파이썬 쪽 기본값(`agents=[]` · `item=None`)이 스키마의 `required` 에서 그 칸을 빼고,
    빠진 칸은 모델에게 **없는 칸처럼 보인다.**

    ```text
    "재고 어때?"              {"action":"STATUS_QUERY","confidence":"HIGH"}   agents 없음
    "오늘 배추 얼마나 사야 해?"  item 이 3/3 으로 null          발화문에 배추가 있는데도
    ```

    `agents` 는 8/28 에 올렸는데 `item` 을 빠뜨렸다. 채점표가 `action` 과 `agents` 만
    보고 있어 **드러나지 않았다** — 8/29 에 관통을 돌려 보고서야 나왔다(품목이 없으면
    마스터가 입력을 못 싣고 매입이 `E4` 로 멈춘다). 채점 항목에 `item` 을 넣었다.

    ★ **`null` 을 못 쓰게 만드는 것이 아니다.** `item` 은 `anyOf[..., null]` 이라
      required 여도 *"모르겠다"* 를 쓸 수 있다. 바뀌는 것은 **매번 판단하게 되는 것**뿐이다.
    """
    schema = Intent.model_json_schema()
    schema["required"] = sorted({*schema.get("required", ()), "agents", "item"})
    return schema


class AnthropicProvider:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        return anthropic_json(
            provider=self.settings.provider,
            model=self.settings.model,
            system=system,
            user=user,
            schema=schema,
            max_tokens=self.settings.max_output_tokens,
            timeout=self.settings.timeout_seconds,
            effort=self.settings.effort,
        )


class OpenAIProvider:
    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        return openai_json(
            provider=self.settings.provider,
            model=self.settings.model,
            system=system,
            user=user,
            schema=schema,
            schema_name="intent",
            max_tokens=self.settings.max_output_tokens,
            timeout=self.settings.timeout_seconds,
        )


class OllamaProvider:
    """Ollama `/api/chat`. 전송 실패(`HTTPError` 포함)는 마스터 문장으로 감싼다."""

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        payload = ollama_request(
            self.settings.model,
            chat_messages(system, user),
            response_format=schema,
            options={
                "temperature": 0,
                "num_ctx": 4096,
                "num_predict": self.settings.max_output_tokens,
            },
        )
        document = send_json(
            ollama_chat_request(self.settings.base_url, payload),
            timeout=self.settings.timeout_seconds,
            failure_message="Master Local LLM request failed",
        )
        return ollama_text(
            document, missing_message="Master Local LLM response did not contain message content"
        )


class GeminiProvider:
    """Gemini REST 호출.

    ★ **API 키는 호출 시점에 환경에서 읽는다** (`MASTER_GEMINI_API_KEY` → `GEMINI_API_KEY`).
      `LLMSettings` 에 담지 않는다 — 설정 객체는 로그·예외에 통째로 실릴 수 있다.
    ★ 자체 재시도가 없다. 재시도는 `IntentService` 가 소유한다.
    🔴 **`HTTPError` 는 감싸지 않는다** — 감싸면 상태 코드가 사라져 429(quota)와 서버 다운이
       로그에서 같아 보였다(실측). 나머지 전송 실패만 마스터 문장으로 감싼다.
    🔴 **`parts[0]` 이 아니다 — 사고 조각이 앞에 오는 모델이 있다.** `gemini-3.5-flash-lite` 는
       `thought: true` 조각을 앞에 붙인다. 첫 조각만 보면 **호출은 성공했는데 FALLBACK** 으로
       떨어진다 — 실측에서 `SELECT_SCENARIO` 가 12번 중 11번 이렇게 죽었다. 사고 조각은 건너뛰고
       빈 문자열이 아닌 첫 글자를 쓴다(공백뿐인 글자도 받는다 — 검증이 되묻는다).
    ★ 응답 스키마는 `gemini_strict_schema` 로 낮춘다 — `X | null` 이 아닌 anyOf 는 조용히 흘리지
      않고 터뜨린다(Gemini 400 이 호출 실패로만 보인다). 길이 제약은 남기고, 못 편 참조는
      `TypeError` 다(옮기기 전 마스터 변환 그대로 — 매입 변환과 다르다).
    """

    def __init__(self, settings: LLMSettings) -> None:
        self.settings = settings

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        api_key = gemini_api_key(_ENV_PREFIX)
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        require_model(self.settings.provider, self.settings.model)
        payload = gemini_json_request(
            system,
            user,
            gemini_strict_schema(schema),
            max_output_tokens=self.settings.max_output_tokens,
        )
        request = gemini_request(
            self.settings.model,
            payload,
            api_key=api_key,
            base_url=scoped_env(_ENV_PREFIX, "GEMINI_BASE_URL", GEMINI_BASE_URL),
        )
        document = send_json(
            request,
            timeout=self.settings.timeout_seconds,
            failure_message="Master Gemini request failed",
            keep_http_errors=True,
        )
        text = first_text(gemini_parts(document), skip_thoughts=True, allow_whitespace=True)
        if text is None:
            raise TypeError("Gemini response did not contain text content")
        return text


class UnavailableProvider:
    """미지원 `LLM_PROVIDER` 값. 조용히 무시하지 않고 **터뜨려 fallback 으로 보낸다**."""

    def generate(self, system: str, user: str, schema: dict[str, Any]) -> str:
        del system, user, schema
        raise RuntimeError("Configured master LLM provider is not supported")


_PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "gemini": GeminiProvider,
}


# ── 검증 ────────────────────────────────────────────────────────────────


class IntentIssue(StrEnum):
    """🔴 **행동을 바꾸는 것만 거부한다.**

    처음에는 "`agents` 는 `STATUS_QUERY` 일 때만" 처럼 **쓰이지도 않는 칸이 차 있는
    것**까지 거부했다. 실측에서 그 엄격함이 손해였다 — 모델이 `PROCUREMENT_RUN` 에
    `agents` 를 곁들이면 거부 → 재시도 → **분류를 UNKNOWN 으로 무르는** 일이 반복됐다.
    쓰지 않는 값이 붙어 있다고 답을 통째로 버리는 셈이었다.

    그래서 셋으로 나눴다.

    ```text
    안 쓰는 칸이 차 있다   →  지운다 (normalize)   — 해가 없다
    필요한 칸이 비었다     →  거부한다             — 부를 대상이 없다
    없던 내용을 지어냈다   →  거부한다             — 그대로 실행에 실린다
    ```
    """

    NOT_JSON = "NOT_JSON"
    SCHEMA = "SCHEMA"
    AGENTS_MISSING = "AGENTS_MISSING"
    AGENT_CANNOT_QUERY = "AGENT_CANNOT_QUERY"
    LABEL_MISSING = "LABEL_MISSING"
    CONDITION_MISSING = "CONDITION_MISSING"
    CONDITION_INVENTED_NUMBER = "CONDITION_INVENTED_NUMBER"
    DOMAIN_ACTION_MISSING = "DOMAIN_ACTION_MISSING"


class IntentValidationError(ValueError):
    def __init__(self, issues: list[IntentIssue]) -> None:
        super().__init__(", ".join(issues))
        self.issues = issues


#: 🔴 교정 문구는 **"빠진 칸을 채워라"** 여야 한다.
#:
#: 처음엔 "STATUS_QUERY 면 agents 를 넣는다" 로만 썼는데, 모델이 그 말을 듣고
#: **분류 자체를 UNKNOWN 으로 바꿔** 회피하는 일이 실측에서 나왔다 ("재고 어때?").
#: 빈 칸을 지적받으면 그 칸을 채우는 대신 **답을 무르는 쪽이 더 쉽기 때문**이다.
#: 그래서 고칠 곳을 짚을 때 **분류를 바꾸지 말라**고 함께 못박는다.
_GUIDANCE: dict[IntentIssue, str] = {
    IntentIssue.NOT_JSON: "JSON 만 출력한다. 설명 문장을 붙이지 않는다.",
    IntentIssue.SCHEMA: "지정된 JSON Schema 의 필드와 허용값만 쓴다.",
    IntentIssue.AGENTS_MISSING: (
        "action 은 STATUS_QUERY 로 그대로 두고 agents 만 채워라. "
        "자금·현금·잔고는 finance, 재고·창고·보관은 inventory 다. "
        "UNKNOWN 으로 바꾸지 마라."
    ),
    IntentIssue.AGENT_CANNOT_QUERY: "그 에이전트는 상태 조회를 받지 않는다. 다른 부서를 골라라.",
    IntentIssue.LABEL_MISSING: (
        "action 은 SELECT_SCENARIO 로 그대로 두고 scenario_label 만 채워라 — "
        "사용자가 부른 이름 그대로(기본 · 보수 · 공격). UNKNOWN 으로 바꾸지 마라."
    ),
    IntentIssue.CONDITION_MISSING: (
        "action 은 RERUN_WITH_CONDITION 으로 그대로 두고 condition 만 채워라 — "
        "사용자의 말 그대로. UNKNOWN 으로 바꾸지 마라."
    ),
    IntentIssue.CONDITION_INVENTED_NUMBER: (
        "condition 에 발화문에 없는 숫자를 넣지 않는다. 사용자의 말 그대로 옮긴다."
    ),
    IntentIssue.DOMAIN_ACTION_MISSING: (
        "action 은 DOMAIN_ACTION 으로 그대로 두고 domain_action 만 허용 목록에서 고른다. "
        "UNKNOWN 으로 바꾸지 마라."
    ),
}


def retry_guidance(issues: list[IntentIssue]) -> list[str]:
    return [_GUIDANCE[issue] for issue in issues]


def normalize_intent(intent: Intent) -> Intent:
    """그 action 에서 **쓰이지 않는 칸을 지운다.**

    모델은 스키마에 있는 칸을 곧잘 곁들여 채운다 — `PROCUREMENT_RUN` 에 `agents`,
    `UNKNOWN` 에 `item` 같은 식으로. 그 값은 **아무도 읽지 않으므로 해가 없다.**
    거부하면 재시도가 돌고, 재시도에서 모델이 답을 무르는 쪽이 훨씬 비싸다.
    """
    action = intent.action
    return intent.model_copy(
        update={
            "agents": list(intent.agents) if action == "STATUS_QUERY" else [],
            "scenario_label": intent.scenario_label if action == "SELECT_SCENARIO" else None,
            "condition": intent.condition if action == "RERUN_WITH_CONDITION" else None,
            "domain_action": intent.domain_action if action == "DOMAIN_ACTION" else None,
            "slots": intent.slots if action == "DOMAIN_ACTION" else None,
            "item": None if action == "UNKNOWN" else intent.item,
        }
    )


def validate_intent(raw_output: str, utterance: str) -> Intent:
    """LLM 출력을 검사한다. **닫힌 열거가 대부분을 막고, 나머지를 여기서 막는다.**

    순서가 중요하다 — **먼저 지우고 나서 검사한다.** 안 쓰는 칸 때문에 답이 버려지지
    않게 하되, 필요한 칸이 빈 것과 지어낸 내용은 그대로 잡는다.
    """
    try:
        intent = Intent.model_validate_json(raw_output)
    except ValidationError as error:
        issue = IntentIssue.NOT_JSON if "json_invalid" in str(error) else IntentIssue.SCHEMA
        raise IntentValidationError([issue]) from error

    intent = normalize_intent(intent)
    issues = _issues(intent, utterance)
    if issues:
        raise IntentValidationError(issues)
    return intent


def _issues(intent: Intent, utterance: str) -> list[IntentIssue]:
    """**정규화 뒤에** 남는 문제만 본다 — 빈 필수 칸과 지어낸 내용."""
    out: list[IntentIssue] = []
    action = intent.action

    if action == "STATUS_QUERY":
        if not intent.agents:
            out.append(IntentIssue.AGENTS_MISSING)
        for agent in intent.agents:
            if "STATUS_QUERY" not in agent_allowed_modes(agent):
                out.append(IntentIssue.AGENT_CANNOT_QUERY)
                break

    if action == "SELECT_SCENARIO" and not (intent.scenario_label or "").strip():
        out.append(IntentIssue.LABEL_MISSING)

    if action == "RERUN_WITH_CONDITION":
        condition = (intent.condition or "").strip()
        if not condition:
            out.append(IntentIssue.CONDITION_MISSING)
        elif _invents_digits(condition, utterance):
            out.append(IntentIssue.CONDITION_INVENTED_NUMBER)

    if action == "DOMAIN_ACTION" and intent.domain_action is None:
        out.append(IntentIssue.DOMAIN_ACTION_MISSING)

    return out


def _invents_digits(condition: str, utterance: str) -> bool:
    """조건의 숫자가 발화문에 없는 숫자인가.

    ★ 매입 ⑤의 "숫자 금지"와 다르다. 여기서는 **사용자가 말한 숫자는 그대로 옮겨야**
      하고, 출처 없는 숫자만 거부한다. 자릿수 단위로 비교하면 "2000"과 "2천"을 구분
      못 하므로 **등장한 숫자 문자의 집합**으로 본다 — 느슨하지만 지어낸 금액은 잡는다.
    """
    return not set(_DIGITS.findall(condition)) <= set(_DIGITS.findall(utterance))


# ── 서비스 ──────────────────────────────────────────────────────────────

#: 확인 없이 바로 실행해도 되는 종류. `PROCUREMENT_RUN` 은 예산 12회와 매입 LLM 을
#: 태우므로 빠져 있다 — 오분류 비용이 비대칭이다.
_NO_CONFIRM_ACTIONS = frozenset({"STATUS_QUERY"})

_UNKNOWN = Intent(action="UNKNOWN", confidence="LOW")

#: 되묻는 말에 쓰는 부서 이름. `answer.py` 와 같은 어휘다.
_DEPT_LABEL = {
    "finance": "재무",
    "inventory": "물류",
    "purchase": "매입",
    "sales": "판매",
    "ml": "가격 예측",
}


class IntentService:
    """검증과 재시도 규칙(무엇을 다시 묻나)을 정한다. **프로바이더가 바뀌어도 이 층은 그대로다.**

    재시도 · fallback 골격은 `app.core.llm.runtime.run_with_fallback` 이다(2026-09-30 BL-020).
    """

    def __init__(self, settings: LLMSettings, provider: TextProvider) -> None:
        self.settings = settings
        self.provider = provider

    def classify(self, utterance: str) -> IntentResult:
        """발화문 하나를 분류한다. **실패하면 UNKNOWN 으로 되묻는다.**

        ★ 실패를 "가장 그럴듯한 것"으로 메우지 않는다. 잘못 분류한 실행은 예산을 태우고,
          사용자는 자기가 안 시킨 일이 도는 것을 본다.
        """
        text = utterance.strip()
        # 빈 발화문이면 부르지 않는다(SKIPPED_TEMPLATE) — 이름 붙일 것이 없다.
        failed_attempts = 0

        def next_guidance(error: Exception) -> list[str] | None:
            """검증 실패는 고칠 곳을 짚어 다시 묻고, 그 밖의 실패는 **다시 묻지 않는다.**

            `run_with_fallback` 은 실패한 시도마다 이것을 한 번 부른다 — 성공 전의 시도는
            모두 실패이므로 여기서 센 횟수가 곧 그때까지의 시도 수다.
            """
            nonlocal failed_attempts
            failed_attempts += 1
            if isinstance(error, IntentValidationError):
                return retry_guidance(error.issues)
            # 🔴 **사유를 버리지 않는다** (2026-09-16). 이 줄이 없어서 **죽은 Gemini 키(403)를
            #    「복수 topic 분류 결함」으로 잘못 짚고 몇 시간을 팠다.** 화면에는 `FALLBACK` 만
            #    떠서 403 인지 429 인지 타임아웃인지 스키마 오류인지 구분이 안 됐다.
            #
            # 🔴 **발화문 원문은 안 싣는다.** 사용자가 친 문장이라 로그에 남길 것이 아니다 —
            #    길이만 적는다.
            #
            # ⚠️ **다시 묻지 않는 것은 그대로다.** 분류 실패가 API 를 죽이면 안 된다는 판단은
            #   맞다. 여기서 하는 일은 **드러내는 것뿐**이다.
            logger.warning(
                "분류 프로바이더 실패 - FALLBACK 으로 되묻는다"
                " (provider=%s · model=%s · 시도 %d회 · 발화문 %d자): %s: %s",
                self.settings.provider,
                self.settings.model,
                failed_attempts,
                len(text),
                type(error).__name__,
                error,
            )
            return None

        intent, status, attempts, fallback = run_with_fallback(
            enabled=self.settings.enabled,
            needs_call=bool(text),
            max_retries=self.settings.max_retries,
            call=lambda guidance: self.provider.generate(
                SYSTEM_PROMPT, _user_payload(text, guidance), _intent_schema()
            ),
            validate=lambda raw: validate_intent(raw, text),
            template=_UNKNOWN,
            guidance_for=next_guidance,
        )
        return self._result(
            intent, status=status, attempts=attempts, fallback=fallback, utterance=text
        )

    def _result(
        self,
        intent: Intent,
        *,
        status: LLMStatus,
        attempts: int,
        fallback: bool,
        utterance: str = "",
    ) -> IntentResult:
        """`utterance` 는 **되물을 말을 고르는 데만** 쓴다.

        분류에는 안 쓴다 — 분류는 이미 끝났고, 여기서 발화문을 다시 보면 규칙이
        모델의 판정을 덮게 된다. 여기서 하는 일은 *"없는 것을 없다고 이름 붙이는 것"*
        뿐이다.
        """
        confirm = _needs_confirmation(intent)
        return IntentResult(
            intent=intent,
            llm_status=status,
            llm_provider=self.settings.provider,
            llm_model=self.settings.model or None,
            llm_attempts=attempts,
            llm_fallback_used=fallback,
            needs_confirmation=confirm,
            clarification=_clarification(intent, utterance) if confirm else None,
        )


def _needs_confirmation(intent: Intent) -> bool:
    if intent.action == "UNKNOWN":
        return True
    if intent.confidence != "HIGH":
        return True
    return intent.action not in _NO_CONFIRM_ACTIONS


#: 🔴 **물어볼 만한데 답할 자리가 없는 것.** 이름을 붙여 준다.
#:
#: *"못 알아들었습니다"* 만 적으면 물어본 사람은 **자기가 말을 잘못했다고 생각하고**
#: 표현을 바꿔 다시 묻는다. 그래도 안 된다 — 없는 것이기 때문이다. 없는 것은
#: **없다고 말해야** 그 사람이 다른 길을 찾는다.
#:
#: ★ 여기 없는 말은 종전대로 일반 안내로 간다. 목록을 늘려 가며 맞히는 것이 아니라,
#:   **자주 묻는데 답이 없는 것**만 이름을 준다.
#:
#: ★ **가격·시세 항목을 뺐다** (2026-09-15). ML 이 상태 조회로 품목 가격에 답하게 되어
#:   더 이상 *"답할 자리가 없는 것"* 이 아니다. 분류 지시문의 UNKNOWN 가격 예시와
#:   **같은 커밋에서** 뺐다 — 한쪽만 남으면 지시문은 ml 로 보내는데 되묻는 말은
#:   *"자리가 없다"* 고 말한다. 목록이 비어도 구조는 남긴다.
#:
#: ★ **제외 품목을 넣었다** (2026-09-16). 피마늘·건고추는 *"기능이 없는 것"* 이 아니라
#:   **이 프로젝트가 다루지 않는 품목**이다. 둘은 다른 사실이므로 *"지원하지 않습니다"*
#:   로 쓰지 않는다. 분류 지시문의 제외 품목 규칙과 **같은 커밋에서** 넣었다 —
#:   한쪽만 있으면 지시문은 부서를 부르는데 되묻는 말은 대상이 아니라고 한다.
#:   근거: 물류 「재고·물류 STATUS_QUERY 기능 정의서」 §3.2 · §3.3 · §16 · §18 · §19 ·
#:   물류 확정 2026-09-16.
_KNOWN_GAPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("피마늘", "건고추"), "피마늘·건고추는 현재 프로젝트의 대상 품목이 아닙니다."),
)


def _known_gap(utterance: str) -> str | None:
    for words, message in _KNOWN_GAPS:
        if any(word in utterance for word in words):
            return message
    return None


def _clarification(intent: Intent, utterance: str = "") -> str:
    """되물을 말. **규칙이 만든다** — LLM 이 쓰면 사용자 응답 생성(⑥)이 되고, 그건 아직 없다."""
    if intent.action == "UNKNOWN":
        gap = _known_gap(utterance)
        if gap:
            return f"{gap} 매입안 생성 · 부서 상태 조회 · 조건 변경 재요청 · 안 선택은 됩니다."
        return (
            "무엇을 해 드릴지 알아듣지 못했습니다. "
            "매입안 생성 · 부서 상태 조회 · 조건 변경 재요청 · 안 선택 중 하나로 말씀해 주세요."
        )
    if intent.action == "PROCUREMENT_RUN":
        item = intent.item or "품목"
        return f"{item} 매입안을 새로 만들까요? (부서 호출이 일어납니다)"
    if intent.action == "STATUS_QUERY":
        # 확신이 낮아 확인받는 경우다. **어느 부서에 물을 것인지** 되읽어 준다.
        names = ", ".join(_DEPT_LABEL.get(a, a) for a in intent.agents) or "부서"
        return f"{names} 상태를 조회할까요?"
    if intent.action == "SELECT_SCENARIO":
        # **무엇을 고른 것으로 알아들었는지 되읽어 준다.** 승인은 되돌리기 어려우므로
        # "진행할까요?" 만 물으면 사용자가 무엇에 동의하는지 모른 채 누른다.
        return f"'{intent.scenario_label}' 안을 고르신 것으로 승인 기록할까요?"
    if intent.action == "RERUN_WITH_CONDITION":
        return f"'{intent.condition}' 조건을 붙여 다시 만들까요?"
    return "이렇게 이해했습니다. 진행할까요?"


def _user_payload(utterance: str, guidance: list[str] | None) -> str:
    payload: dict[str, Any] = {"utterance": utterance}
    if guidance:
        payload["correction"] = guidance
    return json.dumps(payload, ensure_ascii=False)


def build_provider(settings: LLMSettings) -> TextProvider:
    """설정에 맞는 프로바이더. **모르는 값이면 터뜨리는 것을 돌려준다.**

    ★ 역할(①분류 · ⑥응답 생성)마다 프로바이더를 새로 고르게 하지 않는다. 지금은 둘이
      같은 `.env` 를 보지만, **역할마다 모델 등급이 달라지는 것이 예정된 변화**라
      (분류는 소형 · 판정 검증은 상위 모델) 고르는 자리를 한 곳으로 모아 둔다.
    """
    factory = _PROVIDERS.get(settings.provider)
    return factory(settings) if factory else UnavailableProvider()


def get_intent_service() -> IntentService:
    settings = get_llm_settings()
    return IntentService(settings, build_provider(settings))
