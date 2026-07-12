"""환경설정.

환경변수 이름은 루트 공유 인프라(.k8s/config/infra-endpoints.yaml의 GROQ_*,
groq-secret의 GROQ_API_KEY)와 동일하게 맞춘다. 로컬에서는 .env로 주입.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM — Groq (OpenAI 호환 API)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"
    llm_temperature: float = 0.2

    # 백트래킹·수정 한도 (논문 기본값: refine 3/레벨, phase 2, plan 3)
    max_refine_attempts: int = 3
    max_phase_retries: int = 2
    max_plan_retries: int = 3

    # 실행 감사 추적(case folder) 저장 위치
    runs_dir: str = "runs"
