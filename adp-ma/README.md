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

## 로드맵

- case folder MinIO 이전, 실행 큐 Valkey, in-cluster controller 배포(RBAC은 준비됨)
- 병렬 dispatch (autonomous/hybrid), HITL 체크포인트, 비용 추적
- **AutoKaggle** 논문 구조(reader/planner/developer/reviewer 협업 + 단계별 검증)로 확장 — 메타-에이전트 계층과 progressive validation이 그대로 재사용됨
