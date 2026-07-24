"""OpenAI 호환 chat 래퍼 — 역할 티어(주/경량)에 따라 모델·엔드포인트를 라우팅한다.

기본은 단일 클라이언트를 공유하고, 경량 엔드포인트가 지정되면 그 티어만 별도
클라이언트로 보낸다 (예: 주=Groq 70b, 경량=로컬 Ollama). model_for/endpoint_for 참고.
"""

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
        # 교차 엔드포인트: 경량 base_url이 따로 지정될 때만 별도 클라이언트를 만든다.
        # 미지정이면 주 클라이언트를 그대로 재사용해 모델만 달라진다.
        self._client_light = (
            OpenAI(
                base_url=settings.groq_base_url_light,
                api_key=settings.groq_api_key_light,
                max_retries=8,
            )
            if settings.groq_base_url_light
            else self._client
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

    def endpoint_for(self, light: bool = False) -> str:
        """역할 티어 → 실제 엔드포인트. 경량 엔드포인트 미설정 시 주 엔드포인트."""
        if light and self.settings.groq_base_url_light:
            return self.settings.groq_base_url_light
        return self.settings.groq_base_url

    def _client_for(self, light: bool):
        # 경량 모델이 설정된 경우에만 경량 클라이언트를 쓴다 (모델 없이 엔드포인트만 바뀌면 안 됨)
        if light and self.settings.groq_model_light:
            return self._client_light
        return self._client

    def chat(
        self, system: str, user: str, temperature: float | None = None, light: bool = False
    ) -> str:
        """light=True는 저위험·고토큰 역할(요약·서술·도구선택·게이트) — 경량 모델로 라우팅."""
        model = self.model_for(light)
        resp = self._client_for(light).chat.completions.create(
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
