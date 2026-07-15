# ADP-MA — Autonomous Data Processing using Meta-Agents

[arXiv:2602.00307](https://arxiv.org/abs/2602.00307) *Autonomous Data Processing using Meta-Agents* 의 모사 구현.
자연어 목표 하나로 데이터 처리 파이프라인을 자율 계획·구현·검증·복구한다.

LLM 백엔드는 루트 공유 인프라의 결정(Groq API, OpenAI 호환)을 따른다.

## 아키텍처

```
                      ┌────────────────────────────────────────┐
 goal + data ────────▶│ Orchestrator   목표 → phase 계획, 자기비평 │
                      └──────┬─────────────────────────────────┘
                             │ phases
                      ┌──────▼─────────────────────────────────┐
                      │ Architect      phase → ground agent 명세 │
                      │                코드 생성·수정, 라이브러리    │
                      └──────┬─────────────────────────────────┘
                             │ specs + code
                      ┌──────▼─────────────────────────────────┐
                      │ Ground Agents  샌드박스 실행               │
                      │  progressive sampling XS→S→M→FULL       │
                      │  SchemaContract 검증                     │
                      └──────┬─────────────────────────────────┘
                             │ metrics (매 실행마다)
                      ┌──────▼─────────────────────────────────┐
                      │ Monitor        규칙 기반, LLM 호출 없음    │
                      │  continue / warn / retry / abort        │
                      └────────────────────────────────────────┘

  백트래킹:  phase-level (스냅샷 복원 + 재확장, ≤2회/phase)
           plan-level  (오류 증거 누적 + 전체 재계획, ≤3회)
```

## 논문 대비 구현 범위

| 논문 개념 | 상태 | 위치 |
|---|---|---|
| 메타-에이전트 3종 (Orchestrator/Architect/Monitor) | ✅ | `src/adp_ma/meta_agents/` |
| 6단계 파이프라인 사이클 | ✅ | `src/adp_ma/pipeline/runner.py` |
| Progressive sampling (XS/S/M/FULL, refine ≤3/레벨) | ✅ | `src/adp_ma/ground/sampling.py` |
| EnhancedSchemaContract | ✅ (축소) | `src/adp_ma/contracts/` |
| 2단계 백트래킹 (phase ≤2, plan ≤3) | ✅ | `src/adp_ma/pipeline/runner.py` |
| 규칙 기반 Monitor (논문 임계값 표) | ✅ | `src/adp_ma/meta_agents/monitor.py` |
| Case folder 감사 추적 | ✅ | `src/adp_ma/state/case_folder.py` |
| 에이전트 라이브러리 재사용 | ✅ (인메모리) | `src/adp_ma/meta_agents/architect.py` |
| 샌드박스 | 네임스페이스 격리 + **K8s Job 프로세스 격리** (`EXECUTOR=k8s`) | `src/adp_ma/ground/sandbox.py`, `k8s_executor.py` |
| HITL 체크포인트 | ✅ (`--interactive`) | `src/adp_ma/pipeline/runner.py` |
| 병렬 dispatch (autonomous/hybrid) | ⬜ centralized만 | 로드맵 |
| 다중 소스 join, 비용 추적 | ⬜ | 로드맵 |

> AutoKaggle 업그레이드(M1~M4)와 Kaggle 연동은 아래 별도 섹션 참고.

## 사용법

```bash
uv sync
echo "GROQ_API_KEY=..." > .env            # 루트 .k8s/.env와 동일 키

# 데모 데이터 생성 후 실행
uv run python examples/make_sample_data.py
uv run adp-ma run -i examples/sales_raw.csv \
  -g "중복 제거, amount를 숫자로 정규화, region 표준화, 날짜 파싱 후 월별·지역별 매출 집계"

# 결과: runs/<id>/output.csv + decisions.jsonl(감사 추적) + agents/(생성 코드)

uv run pytest          # LLM 없이 도는 결정적 테스트 (contracts/monitor/sampling/sandbox)
```

## K8s Job 실행 (프로세스 격리)

ground agent 1회 실행 = `adp-ma-workers` 네임스페이스의 Job 1개.
controller ↔ worker 데이터 교환은 MinIO(`adp-ma` 버킷, parquet)로 한다.

```bash
# 1회 준비: minio-secret 복제 + RBAC + worker 이미지 빌드 (minikube 내부)
bash .k8s/scripts/setup.sh

# 클러스터 밖(로컬 CLI)에서 디스패치할 때
kubectl port-forward svc/minio 9000:9000 -n portfolio-infra &
EXECUTOR=k8s MINIO_ENDPOINT=http://127.0.0.1:9000 \
  uv run adp-ma run -i examples/sales_raw.csv -g "..."
# (MINIO_ROOT_USER/PASSWORD·GROQ_API_KEY는 .env로 — 루트 .k8s/.env와 동일 키)
```

- Job은 `backoffLimit: 0` — 코드 오류 재시도는 refine 루프가 담당하고, Job 레벨 실패는 인프라 오류로 구분
- worker 파드는 실행 성패를 `status.json`으로 전달하고 항상 정상 종료
- 코드 변경 시 이미지 재빌드 필요: `minikube -p portfolio image build -t adp-ma:0.1.0 adp-ma/`

### case folder 아카이빙 + 입력 URI

- `--archive`(또는 `ARCHIVE_TO_MINIO=true`): 실행 종료 시 case folder 전체를 `minio://adp-ma/runs/<run-id>/`로 업로드 — 파드가 사라진 뒤에도 감사 추적을 사후 복원 가능
- `minio://bucket/key` 입력 URI: 컨트롤러 파드가 클러스터 안에서 데이터를 수급
- `run()`은 예외 안전 — LLM 429 같은 예상 밖 오류에도 `fatal` 기록·`result.json`·아카이브가 반드시 남는다

### in-cluster controller (파이프라인 전체를 클러스터 Job으로)

```bash
# 준비(setup.sh)가 adp-ma-system에 secret·configmap 복제 + controller RBAC 적용
# 입력을 MinIO에 올린 뒤 (예: inputs/sales_raw.csv) 실행
sed 's|<INPUT_URI>|minio://adp-ma/inputs/sales_raw.csv|; s|<GOAL>|...|' \
  .k8s/controller/controller-job.yaml | kubectl create -f -
```

컨트롤러 Job(`adp-ma-system`)이 controller SA로 ground agent Job을 `adp-ma-workers`에 생성하고, 종료 시 case folder를 MinIO로 아카이브한다. 실검증: 워커 Job 15개+ 생성·정리, 429 시 우아한 종료 + 아카이브 확인.

## AutoKaggle 워크플로 (M1~M4 완료)

[설계 문서](docs/autokaggle-design.md)의 전체 마일스톤 구현: M1(tools library + 6-phase),
M2(Reader·Summarizer), M3(모델링·submission), M4(단위 테스트 게이트·HITL·GPU 옵션).

```bash
uv run adp-ma run --workflow kaggle -i train.csv -g "..." \
  [--task-doc task.md] [--test-data test.csv --sample-submission sub.csv [--target y]]
```

- 6-phase 스켈레톤: 배경이해 → 예비 EDA → 클리닝 → 심층 EDA → Feature Engineering → 모델링
- **analysis phase**는 데이터를 바꾸지 않고 인사이트만 생산해 이후 phase의 컨텍스트로 주입 (`runs/<id>/analysis-*.md`)
- **도구 우선 실행**: 검증된 도구 16종(cleaning 8 · feature 8) 카탈로그에서 tool plan을 먼저 시도하고,
  계약 위반·실행 실패 시 codegen으로 폴백. 도구는 검증된 코드라 progressive sampling 없이 즉시 실행
  (계약·Monitor 검증은 동일 적용)
- E2E 검증(70b): 6-phase 완주 — 클리닝 4개 에이전트 중 3개가 도구로 처리, 나머지는 폴백으로 codegen
- 교훈이 된 버그: `convert_dtypes`가 `"$1,234.56"`을 전부 NaN→0으로 만드는 값 파괴 →
  `parse_numeric` 도구 추가 + tool plan 프롬프트에 "안 맞으면 [] 반환" 명시로 해결
- **Reader** (M2): `--task-doc` 과제 문서 + 프로파일 → 구조화 task brief(`brief.md`) — 전 phase 공통 컨텍스트
- **Summarizer** (M2): transform phase 종료마다 실행 이벤트를 압축(`report-<phase>.md`) —
  이후 phase가 "앞에서 무엇을 했는지" 알게 되고, 원시 로그 대신 요약만 컨텍스트에 주입해 토큰 절감
- 실행 종료 시 `report.md` 자동 조립: goal + brief + phase별 요약 + 결과 (사람이 읽는 실행 보고서)
- **모델링** (M3): `--test-data` 지정 시 train+test를 `__adp_split` 마커로 결합해 클리닝·FE를
  동일 적용 → 후보 3모델 CV 비교·선택 → 전체 학습 → test 예측 → `submission.csv`
  (sample_submission 스키마 준수). LLM 무관 검증 코드(sklearn)라 샌드박스 밖에서 실행
- **target 구조적 보호** (M3): LLM 단계가 target을 스케일/변형해도 모델링 직전 id 기준으로
  원본 복원 — 프롬프트 가드(보호 컬럼 명시)는 보조 수단. 보호 컬럼(마커·id) 유실은 백트래킹 처리
- **단위 테스트 게이트** (M4): codegen 경로에서 Architect가 `check(df_in, df_out)` 테스트를
  생성해 M/FULL 승급 전에 실행 — 스키마 계약이 못 잡는 논리 오류 차단. 테스트 자체 결함으로
  실패가 반복되면 게이트를 무력화하고 진행(`gate_disabled` 기록) — 논문 assistance mechanism 축소판
- **HITL 체크포인트** (M4): `--interactive` — 계획 확정 후 실행 전에 승인 요청, 반려 시
  LLM 실행 없이 중단 (AutoKaggle UserInteractionEnabled 대응)
- **GPU Job 옵션** (M4): `WORKER_GPU=true` — worker Job에 `nvidia.com/gpu: 1` 요청
  (루트 cluster-up이 device plugin 설치, RTX 3060)

### VS/CS 벤치마크 (M3, 논문 지표 모사)

`examples/benchmark_model.py` — 합성 churn 분류 과제(통화 문자열·날짜 혼재·중복 오염)로
VS(제출 유효성) + ANPS(accuracy) → **CS = 0.5·VS + 0.5·ANPS** 채점.

2026-07-13, gpt-oss-20b: **VS 1.0 / accuracy 0.865 (다수결 기준선 0.805 초과) / CS 0.9325**
— best: logistic_regression (CV 3모델 비교), 23 LLM calls.

### 품질 게이트 A/B 실험 (M4 단위 테스트 게이트의 효과)

같은 churn 과제·모델(gpt-oss-20b)에서 `UNIT_TESTS_ENABLED` 두 팔 비교:

| 팔 | accuracy | CS | LLM calls | 게이트가 잡은 버그 |
|---|---|---|---|---|
| 게이트 OFF | 0.875 | 0.9375 | 23 | — |
| 게이트 ON | 0.865 | 0.9325 | 18 | 0 (생성된 테스트 2개 모두 `gate_disabled`) |

- **정직한 결론**: 이 과제에서 게이트는 품질 이득이 없었다. 20b가 생성한 단위 테스트가
  오히려 결함이라 안전장치(`gate_disabled`)가 2건 모두 발동 — 게이트가 유효하게 잡은 버그는 0건
- 즉 게이트의 가치는 **강한 모델 + 계약이 못 잡는 논리 버그가 있는 과제**에서 드러나며,
  약한 모델에서는 안전장치가 무해화하는 것이 관측됨. 1회 실행이라 정확도 차(0.01)는 노이즈 범위
- 게이트의 "안전장치가 결함 테스트를 무력화한다"는 설계 의도 자체는 실전에서 정확히 동작함

### 실제 데이터셋 (Titanic)

`examples/benchmark_titanic.py` — 공개 미러에서 Titanic을 받아 train 700/test 191로 분할, VS/CS 채점.
실행 중 **실전 버그 발견·수정**: FE 단계가 중복 컬럼명을 만들면 계약 검증기의 `df[col]`이
Series가 아닌 DataFrame이 되어 크래시 → 중복 컬럼을 critical 위반으로 잡아 refine이 고치도록 수정
([schema_contract.py](src/adp_ma/contracts/schema_contract.py)). 수정 후 클리닝 완주·FE 진입 확인.
(전체 완주 벤치마크 수치는 Groq 무료티어 일일 토큰 한도 리셋 후 기록 예정)

## Kaggle 연동

실제 Kaggle 대회에 연결 — 데이터 다운로드 → 파이프라인 → submission 생성 → (선택) 제출·점수 회수.

```bash
# 인증: ~/.kaggle/kaggle.json 또는 KAGGLE_USERNAME/KAGGLE_KEY (kaggle 표준)
adp-ma kaggle -c titanic -g "결측 보정, 범주형 인코딩, 호칭·가족규모 파생 후 Survived 예측"
# → examples/kaggle_dl/titanic/ 다운로드 → submission.csv 생성 (제출 안 함)

adp-ma kaggle -c titanic -g "..." --submit   # 계정·대회 확인 프롬프트 뒤 실제 제출 → public score
```

- `--submit`는 **외부 공개 동작** — 계정·대회를 화면에 표시하고 확인받은 뒤에만 제출 (기본은 파일만 생성)
- 제출양식 파일명 변종 자동 탐지 (Titanic은 `gender_submission.csv`)
- 실검증: 실제 Titanic 대회 다운로드(34.1k, 계정 인증 확인). 전체 완주·실제 제출은 쿼터 리셋 후

## 품질 벤치마크

`examples/benchmark.py` — 고정 데이터·목표에 대해 산출물을 **결정적 정답**(pandas 직접 계산)과 비교 채점한다.
지표: schema_ok(퍼지)/schema_exact(지시 준수)/sum_rel_err(합계 오차)/group_ratio(그룹 커버리지).

```bash
uv run python examples/benchmark.py --model llama-3.3-70b-versatile --runs 3
```

2026-07-13 측정 (동일 목표: 중복 제거→정규화→표준화→월별·지역별 집계):

| 모델 | 완주 | 합계 오차 | 그룹 | 컬럼명 정확 | 소요 | 판정 |
|---|---|---|---|---|---|---|
| openai/gpt-oss-20b | ✅ 17 calls | **0.0%** | 18/18 | ✅ | 180s | **pass** |
| llama-3.3-70b-versatile | ✅ 21 calls | **0.0%** | 18/18 | ✗ | 60s | **pass** |
| llama-4-scout-17b | ✅ 완주하나 집계 오류 | 76.2% | 31행 | ✗ | 11s | fail |
| llama-3.1-8b-instant | ❌ plan 재시도 소진 | — | — | — | 600s | fail |

- 8b는 스키마 계약 위반(dedup 중 필수 컬럼 유실)을 refine으로 복구하지 못함 —
  프레임워크가 오염된 출력을 정확히 **거부**한 사례 (감사 추적에 전 과정 기록)
- **모델 계층화**: 로컬 개발 기본값 `openai/gpt-oss-20b`(TPD 쿼터가 70b와 분리 — 개발 반복이
  데모 쿼터를 소진하지 않음), 클러스터는 ConfigMap의 `llama-3.3-70b-versatile` env가 덮어씀
- 프롬프트 규칙: 정제 단계는 행 보존(coerce), 집계 단계는 `.agg`(not `.transform`) —
  집계·중복제거처럼 행 감소가 계약된 단계는 Monitor의 행 소실 룰을 WARN으로 완화

## 로드맵

완료: M1~M4, 게이트 A/B, case folder MinIO 아카이빙, in-cluster controller, Kaggle 연동.

- 쿼터 리셋 후 전체 완주 벤치마크 기록 (Titanic VS/CS, controller 완주, 실제 Kaggle 제출 점수)
- 실행 큐 Valkey, 병렬 dispatch (autonomous/hybrid), 비용 추적
- 에이전트 라이브러리 영속화(현재 인메모리)
