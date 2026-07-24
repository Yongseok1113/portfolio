"""환경설정.

환경변수 이름은 루트 공유 인프라(.k8s/config/infra-endpoints.yaml의 GROQ_*,
groq-secret의 GROQ_API_KEY)와 동일하게 맞춘다. 로컬에서는 .env로 주입.
"""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM — OpenAI 호환 엔드포인트 (Groq · Ollama · llama.cpp · vLLM 등)
    # 환경변수는 GROQ_* 또는 provider 중립적인 LLM_* 둘 다 인식한다.
    # 우선순위: LLM_* > GROQ_*  (아래 _apply_llm_aliases 참고)
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_api_key: str = ""
    # 로컬 개발 기본값 — TPD 쿼터가 70b와 분리되고 벤치마크 pass(합계오차 0.0%, schema_exact)
    # 클러스터에서는 infra-endpoints ConfigMap의 GROQ_MODEL(70b) env가 이 기본값을 덮어쓴다
    groq_model: str = "openai/gpt-oss-20b"
    llm_temperature: float = 0.2

    # 역할별 모델 라우팅 — 저위험·고토큰 역할(요약·EDA 서술·도구선택·게이트 테스트)에 쓸
    # 경량 모델. 비워두면 모든 역할이 groq_model 하나를 쓴다(기존 동작).
    # 계획·확장·코드생성·수정은 품질이 결과를 좌우하므로 항상 주 모델을 쓴다.
    groq_model_light: str = ""
    # 교차 엔드포인트 라우팅 — 경량 티어를 다른 엔드포인트(예: 로컬 Ollama)로 보낸다.
    # 비워두면 경량 티어도 주 엔드포인트를 쓰고 모델만 달라진다.
    groq_base_url_light: str = ""
    groq_api_key_light: str = ""

    # provider 중립 별칭 — 로컬 LLM 전환 시 GROQ_* 이름이 헷갈려 LLM_*을 제공.
    # 값이 있으면 대응하는 groq_* 를 덮어쓴다 (없으면 groq_* 유지).
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_model_light: str = ""
    llm_base_url_light: str = ""
    llm_api_key_light: str = ""

    @model_validator(mode="after")
    def _apply_llm_aliases(self):
        if self.llm_base_url:
            self.groq_base_url = self.llm_base_url
        if self.llm_api_key:
            self.groq_api_key = self.llm_api_key
        if self.llm_model:
            self.groq_model = self.llm_model
        if self.llm_model_light:
            self.groq_model_light = self.llm_model_light
        if self.llm_base_url_light:
            self.groq_base_url_light = self.llm_base_url_light
        if self.llm_api_key_light:
            self.groq_api_key_light = self.llm_api_key_light
        # 로컬 서버는 키가 없어도 되지만 OpenAI 클라이언트가 빈 문자열을 거부하므로 더미 채움
        if not self.groq_api_key and "api.groq.com" not in self.groq_base_url:
            self.groq_api_key = "local"
        # 경량 엔드포인트만 주고 경량 모델을 안 주면 라우팅이 성립하지 않는다 — 설정 오류로 알림
        if self.groq_base_url_light and not self.groq_model_light:
            raise ValueError(
                "경량 엔드포인트(*_BASE_URL_LIGHT)를 지정하면 경량 모델(*_MODEL_LIGHT)도 필요합니다"
            )
        if (
            self.groq_base_url_light
            and not self.groq_api_key_light
            and "api.groq.com" not in self.groq_base_url_light
        ):
            self.groq_api_key_light = "local"
        return self

    # 백트래킹·수정 한도 (논문 기본값: refine 3/레벨, phase 2, plan 3)
    max_refine_attempts: int = 3
    max_phase_retries: int = 2
    max_plan_retries: int = 3

    # 실행 감사 추적(case folder) 저장 위치
    runs_dir: str = "runs"

    # ── 워크플로 (AutoKaggle M1) ─────────────────────────────────────────
    # dynamic: Orchestrator가 phase를 동적 계획 / kaggle: 고정 6-phase 스켈레톤
    workflow: str = "dynamic"
    # 도구 우선 실행 (ML tools library) — 끄면 항상 codegen
    tools_enabled: bool = True
    # 단위 테스트 게이트 (M4) — codegen 경로의 M/FULL 승급 전 논리 검증
    unit_tests_enabled: bool = True

    # ── ground agent 실행 방식 ───────────────────────────────────────────
    # local: 인프로세스 샌드박스 / k8s: adp-ma-workers ns의 Job (프로세스 격리)
    executor: str = "local"
    worker_image: str = "adp-ma:0.1.0"
    worker_namespace: str = "adp-ma-workers"
    job_timeout_s: int = 600
    # worker Job에 GPU 1개 요청 (nvidia device plugin 필요 — 루트 cluster-up이 설치)
    worker_gpu: bool = False

    # MinIO — 루트 infra-endpoints ConfigMap / minio-secret과 동일 키
    minio_endpoint: str = "http://minio.portfolio-infra.svc.cluster.local:9000"
    # 클러스터 밖에서 디스패치할 때 위 값을 port-forward 주소로 바꾸더라도,
    # worker Job에는 항상 클러스터 내부 주소를 주입한다
    minio_endpoint_incluster: str = "http://minio.portfolio-infra.svc.cluster.local:9000"
    minio_root_user: str = ""
    minio_root_password: str = ""
    minio_bucket: str = "adp-ma"
    # 실행 종료 시 case folder를 MinIO(runs/<run-id>/)로 업로드 — 실행 결과 영속화
    archive_to_minio: bool = False
