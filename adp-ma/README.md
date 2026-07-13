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
| 병렬 dispatch (autonomous/hybrid) | ⬜ centralized만 | 로드맵 |
| 다중 소스 join, 비용 추적, HITL 체크포인트 | ⬜ | 로드맵 |

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

## AutoKaggle 워크플로 (M1)

[설계 문서](docs/autokaggle-design.md)의 M1 구현 — **ML tools library + 고정 6-phase 워크플로**.

```bash
uv run adp-ma run --workflow kaggle -i data.csv -g "..."
```

- 6-phase 스켈레톤: 배경이해 → 예비 EDA → 클리닝 → 심층 EDA → Feature Engineering → 모델링(M3 예정)
- **analysis phase**는 데이터를 바꾸지 않고 인사이트만 생산해 이후 phase의 컨텍스트로 주입 (`runs/<id>/analysis-*.md`)
- **도구 우선 실행**: 검증된 도구 16종(cleaning 8 · feature 8) 카탈로그에서 tool plan을 먼저 시도하고,
  계약 위반·실행 실패 시 codegen으로 폴백. 도구는 검증된 코드라 progressive sampling 없이 즉시 실행
  (계약·Monitor 검증은 동일 적용)
- E2E 검증(70b): 6-phase 완주 — 클리닝 4개 에이전트 중 3개가 도구로 처리, 나머지는 폴백으로 codegen
- 교훈이 된 버그: `convert_dtypes`가 `"$1,234.56"`을 전부 NaN→0으로 만드는 값 파괴 →
  `parse_numeric` 도구 추가 + tool plan 프롬프트에 "안 맞으면 [] 반환" 명시로 해결

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

- **M2**: Reader(대회 문서 이해) + Summarizer(phase report) / **M3**: 모델링 phase + submission + VS/CS / **M4**: 단위 테스트 게이트 + HITL
- case folder MinIO 이전, 실행 큐 Valkey, in-cluster controller 배포(RBAC은 준비됨)
- 병렬 dispatch (autonomous/hybrid), 비용 추적
