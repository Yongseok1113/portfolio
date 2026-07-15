# portfolio

Kubernetes 위에서 여러 독립 프로젝트를 굴리는 **모노레포형 포트폴리오**.
루트에 공통 인프라(`.k8s/`)를 한 번 프로비저닝해두고, 그 위에 각자 독립된 하위 프로젝트를 올린다.
LLM은 클러스터 밖 외부 API(Groq)를 사용해 GPU 자원을 학습·추론 워크로드에 집중시킨다.

- 관리: `git` + `uv` (Python), 실행 환경: `minikube` (로컬 단일 노드 K8s)
- 개발 환경: WSL2(Ubuntu) + Windows, GPU: RTX 3060 Laptop

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│  portfolio-infra (공유 인프라 · 루트가 소유)                    │
│    Valkey(캐시/큐)   PostgreSQL(메타데이터)   MinIO(오브젝트)   │
│    LLM → 외부 Groq API (OpenAI 호환, 클러스터 밖)              │
└─────────────────────────────────────────────────────────────┘
        ▲ endpoints(ConfigMap) + secret 참조
        │
┌───────┴───────────────┐   ┌───────────────────────┐
│  adp-ma               │   │  aquarium             │
│  <name>-system        │   │  <name>-system        │
│  <name>-workers       │   │  <name>-workers       │
│  (프로젝트별 독립 ns)   │   │                       │
└───────────────────────┘   └───────────────────────┘
```

각 프로젝트는 `<project>-system` / `<project>-workers` 두 네임스페이스를 갖고, 공유 인프라 엔드포인트는
`infra-endpoints` ConfigMap(주소)과 Secret(비밀값)으로만 참조한다. 프로젝트 간 결합은 없다.

---

## 디렉토리 구조

```
portfolio/
├── .k8s/                      ← 공유 인프라 (루트 소유)
│   ├── infra/                 ← Valkey·PostgreSQL(helm values), MinIO(manifest)
│   ├── namespaces/            ← 프로젝트 ns 선언 + _template.yaml
│   ├── config/                ← infra-endpoints ConfigMap (주소만, 비밀값 제외)
│   ├── rbac/                  ← cross-namespace grant
│   ├── scripts/               ← cluster-up/down/clean/status, create-secrets
│   └── .env                   ← 비밀값 (gitignore)
│
├── adp-ma/                    ← [성숙] 메타-에이전트 자율 데이터 파이프라인 + AutoKaggle + Kaggle 연동
│   ├── src/adp_ma/            ← 메타-에이전트, 파이프라인, tools library, 모델링, kaggle_io
│   ├── .k8s/                  ← ground agent Job(프로세스 격리) + in-cluster controller
│   ├── examples/              ← 데모 데이터 · 채점형 벤치마크(합성·Titanic·모델링)
│   ├── tests/                 ← LLM 불필요 결정적 테스트 (72개)
│   └── docs/                  ← AutoKaggle 업그레이드 설계
│
├── aquarium/                  ← [초기] 멀티 에이전트 시스템 (스캐폴드)
│
├── docs/
│   └── GIT_CONVENTION.md      ← 브랜치 전략
└── README.md
```

---

## 개발 현황

| 영역 | 상태 | 요약 |
|---|---|---|
| **공유 인프라** (`.k8s/`) | ✅ 운영 | Valkey·PostgreSQL·MinIO 프로비저닝 완료, Groq LLM 전환, 운영 스크립트 일습 |
| **adp-ma** | ✅ 성숙 | 논문 모사 파이프라인 + AutoKaggle 업그레이드(M1~M4) + Kaggle 연동 + in-cluster controller |
| **aquarium** | 🟡 스캐폴드 | uv 프로젝트 뼈대만 존재, 구현 예정 |

### 공유 인프라 (`.k8s/`)

`minikube` 단일 노드 클러스터(`portfolio`, 8 CPU / 14 GB / K8s v1.29) 위에 공통 백엔드를 배포한다.

- **Valkey** (Redis 호환) · **PostgreSQL** — Helm, **MinIO** — raw manifest, 모두 `portfolio-infra` ns
- **LLM은 외부 Groq API** — 클러스터 내 모델 배포 없음. 기본 모델 `llama-3.3-70b-versatile`
- 비밀값은 `.k8s/.env`(gitignore) → `create-secrets.sh`로 Secret 생성. ConfigMap에는 엔드포인트만
- `cluster-up.sh`로 클러스터 기동 + 인프라 배포 + 선택적 프로젝트 배포를 원커맨드로 처리

```bash
cd .k8s/scripts
./cluster-up.sh                    # 클러스터 + 공유 인프라
./cluster-up.sh --project adp-ma   # + adp-ma 앱까지
./cluster-status.sh                # 상태 확인
```

### adp-ma — 자율 데이터 처리 (Autonomous Data Processing using Meta-Agents)

[arXiv:2602.00307](https://arxiv.org/abs/2602.00307) 모사. **자연어 목표 하나로 데이터 파이프라인을
자율 계획 → 코드 생성 → 검증 → 복구**한다. 상세는 [adp-ma/README.md](adp-ma/README.md).

- **메타-에이전트 3종**: Orchestrator(계획·자기비평) / Architect(에이전트 확장·코드 생성) / Monitor(규칙 기반 감시)
- **Progressive sampling** (XS→S→M→FULL) + **스키마 계약** 검증 + **2단계 백트래킹**(phase/plan)
- **K8s Job 프로세스 격리**: ground agent 1회 실행 = `adp-ma-workers` ns의 Job 1개, MinIO로 데이터 교환
- **case folder** 감사 추적: 계획·생성 코드·결정 로그를 실행별로 보존 (`--archive`로 MinIO 영속화)
- **채점형 벤치마크**: 결정적 정답 대비 측정 → 70b 합계오차 0.0% pass / 8b는 계약 위반을 프레임워크가 정확히 거부
- **[AutoKaggle 업그레이드](adp-ma/docs/autokaggle-design.md) 완성** (M1~M4): ML tools library(도구 우선·codegen 폴백),
  고정 6-phase 워크플로, Reader·Summarizer, 모델링·submission(VS/CS), 단위 테스트 게이트·HITL
- **Kaggle 연동**: `adp-ma kaggle -c <slug> -g <goal>` — 대회 데이터 다운로드 → 파이프라인 → submission → (확인 후) 제출
- **in-cluster controller**: 파이프라인 전체를 `adp-ma-system`의 Job으로 실행 (controller SA가 워커 Job 오케스트레이션)

### aquarium

멀티 에이전트 시스템을 목표로 한 uv 기반 Python 프로젝트. 현재는 스캐폴드 단계.

---

## 브랜치 전략

[docs/GIT_CONVENTION.md](docs/GIT_CONVENTION.md) 참고.

```
main (보호)
└── develop/<project> (보호, PR만 머지)
    ├── feat/<project>/<name>
    ├── fix/<project>/<name>
    └── k8s/<project>/<name>

k8s/infra/<name>            ← 루트 공유 인프라 (독립)
```

- 소문자 + 하이픈, 커밋은 `type(scope): 설명` (예: `feat(adp-ma): ...`, `k8s(infra): ...`)
- `main`·`develop/*`는 직접 push 금지, PR로만 머지

---

## Quick Start

```bash
# 1. 공유 인프라 기동
cd .k8s/scripts && ./cluster-up.sh

# 2. adp-ma 실행 (LLM 목표 → 데이터 파이프라인)
cd ../../adp-ma
uv sync
uv run python examples/make_sample_data.py
uv run adp-ma run -i examples/sales_raw.csv \
  -g "중복 제거, amount 정규화, region 표준화, 월별·지역별 매출 집계"

# 3. Kaggle 대회 (다운로드 → 파이프라인 → submission, --submit로 실제 제출)
uv run adp-ma kaggle -c titanic -g "결측 보정·인코딩·파생 후 Survived 예측"

uv run pytest    # LLM 없이 도는 결정적 테스트 (72개)
```
