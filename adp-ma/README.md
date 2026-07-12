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
| 샌드박스 | 네임스페이스 격리 (프로세스 격리는 K8s Job 로드맵) | `src/adp_ma/ground/sandbox.py` |
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

## K8s 통합 (로드맵)

- 루트 `infra-endpoints` ConfigMap의 `GROQ_BASE_URL`/`GROQ_MODEL`, `groq-secret`의 `GROQ_API_KEY`를 그대로 env로 주입 — 코드 수정 불필요
- ground agent 실행을 `adp-ma-workers` 네임스페이스의 K8s Job으로 분리 → 논문의 프로세스 격리 확보
- case folder를 MinIO로, 실행 큐를 Valkey로 이전
- 이후 **AutoKaggle** 논문 구조(reader/planner/developer/reviewer 협업 + 단계별 검증)로 확장 — 메타-에이전트 계층과 progressive validation이 그대로 재사용됨

## 품질 벤치마크

`examples/benchmark.py` — 고정 데이터·목표에 대해 산출물을 **결정적 정답**(pandas 직접 계산)과 비교 채점한다.
지표: schema_ok(퍼지)/schema_exact(지시 준수)/sum_rel_err(합계 오차)/group_ratio(그룹 커버리지).

```bash
uv run python examples/benchmark.py --model llama-3.3-70b-versatile --runs 3
```

2026-07-13 측정 (동일 목표: 중복 제거→정규화→표준화→월별·지역별 집계):

| 모델 | 완주 | 합계 오차 | 그룹 | 소요 | 판정 |
|---|---|---|---|---|---|
| llama-3.3-70b-versatile | ✅ 21 calls | **0.0%** | 18/18 | 60s | **pass** |
| llama-3.1-8b-instant | ❌ plan 재시도 소진 | — | — | 600s | fail |

- 8b는 스키마 계약 위반(dedup 중 필수 컬럼 유실)을 refine으로 복구하지 못함 —
  프레임워크가 오염된 출력을 정확히 **거부**한 사례 (감사 추적에 전 과정 기록)
- 개발·데모에는 `GROQ_MODEL=llama-3.3-70b-versatile` 권장
- 프롬프트 규칙: 정제 단계는 행 보존(coerce), 집계 단계는 `.agg`(not `.transform`) —
  집계·중복제거처럼 행 감소가 계약된 단계는 Monitor의 행 소실 룰을 WARN으로 완화
