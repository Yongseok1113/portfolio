"""Groq(OpenAI 호환) chat 래퍼 — 모든 메타-에이전트가 하나의 클라이언트를 공유한다."""

import json
import re

from openai import OpenAI

from adp_ma.config import Settings


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = OpenAI(
            base_url=settings.groq_base_url,
            api_key=settings.groq_api_key,
            # Groq 무료 티어 TPM 한도(429)는 Retry-After 대기 후 재시도로 흡수
            max_retries=8,
        )
        self.calls = 0
        self.total_tokens = 0
        # 모델별 사용량 — 라우팅 효과 측정·비용 추적용
        self.calls_by_model: dict[str, int] = {}
        self.tokens_by_model: dict[str, int] = {}

    def model_for(self, light: bool = False) -> str:
        """역할 티어 → 실제 모델명. 경량 모델 미설정 시 주 모델로 폴백."""
        if light and self.settings.groq_model_light:
            return self.settings.groq_model_light
        return self.settings.groq_model

    def chat(
        self, system: str, user: str, temperature: float | None = None, light: bool = False
    ) -> str:
        """light=True는 저위험·고토큰 역할(요약·서술·도구선택·게이트) — 경량 모델로 라우팅."""
        model = self.model_for(light)
        resp = self._client.chat.completions.create(
            model=model,
            temperature=(
                self.settings.llm_temperature if temperature is None else temperature
            ),
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        self.calls += 1
        self.calls_by_model[model] = self.calls_by_model.get(model, 0) + 1
        if resp.usage:
            self.total_tokens += resp.usage.total_tokens
            self.tokens_by_model[model] = (
                self.tokens_by_model.get(model, 0) + resp.usage.total_tokens
            )
        return resp.choices[0].message.content or ""

    def chat_json(
        self, system: str, user: str, temperature: float | None = None, light: bool = False
    ):
        """JSON 응답을 요구하고, 코드펜스·잡문이 섞여도 첫 JSON 값을 복원한다."""
        raw = self.chat(
            system + "\nRespond with a single JSON value and nothing else.",
            user,
            temperature,
            light=light,
        )
        return extract_json(raw)


def extract_json(raw: str):
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start, end = raw.find(open_c), raw.rfind(close_c)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"LLM 응답에서 JSON 파싱 실패: {raw[:200]!r}")


def extract_code(raw: str) -> str:
    fence = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
    return (fence.group(1) if fence else raw).strip()
