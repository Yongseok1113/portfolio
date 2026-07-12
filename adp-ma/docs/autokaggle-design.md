# ADP-MA → AutoKaggle 업그레이드 설계

- 대상 논문: [AutoKaggle: A Multi-Agent Framework for Autonomous Data Science Competitions (arXiv:2410.20424)](https://arxiv.org/abs/2410.20424)
- 현재 기반: [ADP-MA (arXiv:2602.00307)](https://arxiv.org/abs/2602.00307) 모사 구현 — 메타-에이전트 3종, progressive sampling, 스키마 계약, 2단계 백트래킹
- 작성: 2026-07-13

## 1. 두 논문의 관계

ADP-MA는 **범용 데이터 처리 파이프라인**(계획→코드생성→검증→복구)이고,
AutoKaggle은 이를 **데이터 사이언스 경진 워크플로**(EDA→클리닝→FE→모델링→제출)로 특화한 것에 가깝다.
핵심 실행 루프(코드 생성→실행→디버그→검증)는 동일 계보라서 현 코드베이스 대부분이 재사용된다.

### 에이전트 매핑

| AutoKaggle | ADP-MA 현재 구현 | 계획 |
|---|---|---|
| **Planner** (phase별 태스크 ≤4 분해) | Orchestrator (동적 phase 계획) | 역할 전환: 고정 6-phase 스켈레톤 안에서 phase 내부 태스크만 계획 |
| **Developer** (코드 생성·실행·디버그·단위테스트) | Architect + ground agent + refine 루프 | 유지 + 단위 테스트 생성 추가 |
| **Reviewer** (산출물 품질 평가) | Monitor (규칙 기반) | Monitor 유지 + LLM 리뷰 패스 추가 (이원화: 규칙=cheap, LLM=deep) |
| **Reader** (대회 문서 → 구조화 요약) | 없음 (profiling이 데이터만 요약) | 신규 — 문서(overview/description) 이해 단계 |
| **Summarizer** (phase 리포트·최종 문서) | case folder (원시 감사 추적) | 신규 — case folder 위에 리포트 생성층 |

### 실행 메커니즘 매핑

| AutoKaggle | ADP-MA 현재 | 판단 |
|---|---|---|
| self-correct ≤5회/phase, phase 반복 ≤3회 | refine ≤3/레벨, phase ≤2, plan ≤3 | 한도 값만 설정으로 조정 (구조 동일) |
| 반복 유사 오류 시 코드 재생성 탈출 | plan-level 백트래킹 | 이미 보유 — 오류 패턴 매칭만 추가 |
| 단위 테스트 (논리 정합성) | SchemaContract (구조 정합성) + progressive sampling | 계약은 유지, LLM 생성 단위테스트를 FULL 승급 전 게이트로 추가 |
| ML tools library (클리닝 7, FE 11, 모델링 1) | Architect의 검증 에이전트 라이브러리 (동적) | 정적 시드로 이식 — 도구 호출 우선, 코드 생성은 폴백 |
| HITL 체크포인트 (UserInteractionEnabled) | 없음 (ADP-MA 논문에도 로드맵) | 계획 승인 지점 1곳부터 |

## 2. 목표 아키텍처

```
Reader ──▶ 대회/과제 요약 ─┐
                          ▼
        ┌─ 6-Phase Workflow (고정 스켈레톤) ─────────────────┐
        │ ① 배경 이해  ② 예비 EDA  ③ 클리닝                  │
        │ ④ 심층 EDA  ⑤ Feature Engineering  ⑥ 모델링·제출   │
        └───────┬────────────────────────────────────────┘
                │ phase마다
                ▼
   Planner(태스크 ≤4) ─▶ Developer(도구 우선, 코드생성 폴백)
                │              │ K8s Job (adp-ma-workers)
                │              ▼
                │        progressive sampling + SchemaContract
                │              + 단위 테스트 게이트
                ▼              ▼
   Reviewer(Monitor 규칙 + LLM 리뷰) ─▶ Summarizer(phase report)
                │
                └─ 실패 시: self-correct → phase 반복 → 재계획 (기존 2단계 백트래킹)
```

- **상태 관리**: 기존 case folder를 확장 — phase별 `report.md`(Summarizer), 이전 phase의 코드·출력·리포트가 다음 phase의 컨텍스트
- **K8s**: phase 태스크 = Job (기존 K8sJobExecutor 재사용). 모델 학습 Job에는 GPU nodeSelector 옵션 (RTX 3060 활용)

## 3. 신규 컴포넌트 상세

### 3.1 ML Tools Library (`src/adp_ma/tools/`)
AutoKaggle의 검증된 함수 집합을 이식. Developer는 목표를 먼저 도구 조합으로 시도하고, 부족할 때만 코드 생성.
- cleaning: fill_missing / remove_sparse_columns / outliers_zscore / outliers_iqr / dedup / convert_dtypes / format_datetime (7)
- feature: onehot / frequency_encode / scale / correlation_select 등 (11)
- modeling: train_validate_select (모델 선택·학습·평가·예측·앙상블, sklearn)
- 각 도구는 SchemaContract를 내장 선언 → 검증 비용 절감 (LLM이 계약을 만들 필요 없음)

### 3.2 단위 테스트 게이트
FULL 승급 전, Developer가 phase 목표 기반 단위 테스트(pytest 스타일 assert 함수)를 생성해 M 샘플 결과에 실행.
SchemaContract가 못 잡는 논리 오류(예: 인코딩 후 카디널리티, 스케일링 범위)를 차단.

### 3.3 Reader / Summarizer
- Reader: `task.md`(대회 overview 상당) + 데이터 샘플 → 구조화 요약(목표·평가지표·파일 구조). 요약이 전 phase의 공통 컨텍스트가 됨
- Summarizer: phase 종료마다 case folder 기록을 리포트로 압축 — 다음 phase 프롬프트 컨텍스트 축소(토큰 절감) + 인간 가독성

### 3.4 평가 (논문 지표 모사)
- **VS** (valid submission rate): submission.csv가 sample_submission 스키마와 일치하는 비율
- **CS** = 0.5×VS + 0.5×ANPS (정규화 성능: 최소화 지표는 1/(1+s), 최대화는 원점수)
- 데이터: Kaggle 형식 로컬 데이터셋(train/test/sample_submission)으로 시작 (Titanic 등) — 기존 `examples/benchmark.py` 채점 프레임을 확장

## 4. 마일스톤

| 단계 | 내용 | 산출 |
|---|---|---|
| **M1** | tools library 시드 + 고정 6-phase 워크플로 (기존 러너 위) | `k8s/adp-ma`의 Job 실행과 결합, 클리닝·FE까지 완주 |
| **M2** | Reader + Summarizer + phase report | 대회 문서 입력 지원, 컨텍스트 토큰 절감 측정 |
| **M3** | 모델링 phase (sklearn) + submission 생성 | Titanic 로컬 벤치마크 VS/CS 측정 |
| **M4** | 단위 테스트 게이트 + HITL 체크포인트 + GPU Job | 품질 게이트 비교 실험 (계약만 vs 계약+테스트) |

각 마일스톤 = 브랜치 1개(`feat/adp-ma/autokaggle-m1` …) + 벤치마크 리포트 갱신.

## 5. 리스크

- **모델 크기**: 70b 기준으로도 모델링 코드 생성은 8b 대비 훨씬 무겁다 — Groq TPM 한도에서 phase당 토큰 예산 관리 필요 (Monitor의 비용 추적 로드맵을 M3에서 함께 구현)
- **고정 워크플로 vs 동적 계획**: 범용 목표(현 benchmark)는 동적 계획이 맞음 — 워크플로를 템플릿화해 `--workflow kaggle|dynamic` 스위치로 양쪽 유지
- **sklearn 샌드박스**: import 화이트리스트에 sklearn 추가 시 K8s Job 격리를 기본 executor로 승격하는 것이 안전
