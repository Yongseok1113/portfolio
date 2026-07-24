# 공용 인프라 MCP 서버 설계

여러 프로젝트(adp-ma, aquarium…)가 공유하는 **공통 툴을 하나의 MCP 서버로 노출**한다.
GPU 학습 job이 첫 소비자이지만, 이 문서는 *툴 하나*가 아니라 *툴을 관리하는 서버*를 설계한다.

> 상태: 설계. GPU job 툴의 실제 구성 가능성(Kaggle Kernels 실측)은 별도 후속 단계.
> SDK 사실은 검증됨 — `mcp` 1.28.1, `mcp.server.fastmcp.FastMCP`, 전송 `stdio`/`sse`/`streamable-http`.

## 1. 왜 인프라 수준인가

루트 원칙(README): *"공유 인프라 엔드포인트는 ConfigMap·Secret으로만 참조, 프로젝트 간 결합 없음."*
Valkey·PostgreSQL·MinIO·Groq가 이미 이 패턴이다. MCP 서버가 노출할 자원도 같은 성질을 가진다:

- **희소하고 계정에 묶임** — Kaggle GPU 쿼터(주 ~30h)는 계정 단위이지 프로젝트 단위가 아니다.
  프로젝트마다 툴을 들면 하나의 예산을 조율 없이 경쟁하고, 누가 얼마 썼는지 추적이 안 된다.
  (이번 개발에서 Groq TPD를 한 프로젝트가 태워 하루 막힌 경험 — 두 프로젝트였다면 원인 파악조차 어려웠다)
- **중앙에서 관리할 것** — 자격증명, 쿼터 회계, 레이트리밋, 감사 로그.

분할선: **희소·계정단위·공통 = 인프라 MCP. 도메인 로직 = 프로젝트 MCP(별도).**
adp-ma 파이프라인(`profile`/`start_run`/`run_status`)은 이 서버가 아니라 프로젝트 MCP로 간다.

## 2. 아키텍처

```
MCP 클라이언트 (Claude Desktop/Code, 에이전트 런타임)
        │  JSON-RPC (stdio 로컬 / streamable-http 클러스터)
        ▼
┌──────────────────────────────────────────────┐
│ mcp-infra  (FastMCP 서버, portfolio-infra ns) │
│                                                │
│   툴 레지스트리 ──▶ [gpu_job] [향후 툴...]      │
│        │                │                       │
│        │           동기: request-response       │
│        │           비동기: submit→id→poll        │
│        ▼                                         │
│   상태(Valkey)   산출물(MinIO)   시크릿(Secret)  │
└──────────────────────────────────────────────┘
        │ 참조                    │ 위임
        ▼                        ▼
  infra-endpoints ConfigMap   외부 API (Kaggle 등)
```

- **FastMCP** — 툴은 `@mcp.tool()` 데코레이터 함수, 입출력 스키마는 타입힌트에서 자동 생성.
  리소스는 `@mcp.resource("job://{id}")`, 프롬프트는 `@mcp.prompt()`.
- **상태는 Valkey** — job 매핑·큐·쿼터 카운터. 메모리에 두면 pod 재시작 시 유실(이전 설계 리뷰의 지적).
- **산출물은 MinIO** — PR #15 아카이빙과 동일 경로 재사용. 툴 산출물을 `mcp/<tool>/<job>/`에 적재.

## 3. 툴 인터페이스 계약 (이 설계의 핵심)

"툴 관리"란 툴들이 **균일한 계약**으로 등록·호출되게 하는 것이다. 두 종류로 나눈다.

### 동기 툴 (짧음)
```python
@mcp.tool()
def tool_name(arg: str) -> Result: ...   # request-response, 스키마 자동
```

### 비동기 툴 (긴 작업 — 학습 job 등)
MCP 호출은 클라이언트 타임아웃이 있는 request-response다. 수십 분짜리 작업은
**즉시 job_id를 반환하고 폴링**해야 한다. 모든 비동기 툴은 3종 세트를 따른다:

| 규약 | 반환 | 매핑 |
|---|---|---|
| `submit_<x>(spec) -> job_id` | 즉시 | 외부 API에 제출 + Valkey에 매핑 기록 |
| `<x>_status(job_id) -> {state, ...}` | 즉시 | 상태 폴링 |
| `<x>_output(job_id) -> uri` | 즉시 | 산출물 → MinIO uri |

- 부수효과가 있는 제출은 **POST 성격** — GET(조회)과 구분(이전 리뷰).
- 리소스로도 노출: `job://<x>/<id>` → 상태·산출물 링크 (파드가 사라져도 Valkey/MinIO에 살아있음).
- **자율 호출 주의**: 되돌리기 어렵거나 쿼터를 크게 먹는 툴(외부 제출)은 기본 비활성 또는
  확인 게이트. adp-ma에서 `kaggle_submit`을 MCP에 노출하지 않은 원칙과 동일.

### 툴 등록
`tools/` 하위 모듈이 `register(mcp)` 함수를 노출하고, 서버 기동 시 자동 발견해 등록.
새 툴 추가 = 모듈 하나 + register 한 줄. GPU job과 미래 툴이 같은 방식으로 붙는다.

## 4. 상태·영속성 (재시작 안전)

Valkey 키:
- `job:<id>` → {tool, requester, state, output_uri, created_at, expires_at}
- `quota:<resource>:<window>` → 사용량 카운터 (예: `quota:kaggle-gpu:week`)

재시작 reconciliation: 기동 시 Valkey의 in-flight job을 외부 API 상태와 대조,
고아(orphaned) job 정리. 그래야 pod 재시작 후에도 외부 쪽 자원이 방치되지 않는다.

## 5. 배포

| 환경 | 전송 | 방법 |
|---|---|---|
| 로컬 개발 | `stdio` | `uv run mcp-infra` → 클라이언트 설정에 command 등록 |
| 클러스터 | `streamable-http` | `portfolio-infra` ns Deployment, `streamable_http_app()` ASGI |

- **replica=1** — 계정 인증·쿼터 회계가 싱글톤 자원이면 다중 레플리카가 상태 충돌을 일으킨다.
  (Valkey에 상태를 두면 완화되나, 외부 세션이 싱글톤이면 리더 일렉션 필요 — 지금은 1로 고정)
- 시크릿: `kaggle-secret`(케밥케이스 키 — `groq-secret`/`minio-secret` 컨벤션). `create-secrets.sh` 확장.
- `infra-endpoints` ConfigMap에 MCP 서버 주소 추가.
- 헬스체크: `health` 툴 또는 HTTP `/health`.

## 6. 인증·보안 (1인 규모라 경량)

- 지금: stdio는 로컬 신뢰. http는 NetworkPolicy + 정적 토큰(헤더 검사) 정도.
- **게이트웨이·federation·RBAC·capability token은 만들지 않는다** — 다수 팀·다수 툴서버의 관행이고
  여기는 1인·소비자 소수. MCP 클라이언트에 서버를 여러 개 등록하는 것으로 충분.
- 소비자가 3~4개로 늘고 접근 제어가 실제 문제일 때 게이트웨이를 얹는다(그때도 늦지 않음).

## 7. 첫 소비자: GPU job (인터페이스 수준만)

Kaggle Kernels API가 헤드리스 배치를 공식 지원함은 확인됨(`kernels push/status/output/logs`).
비동기 3종 세트에 그대로 매핑된다:

```
submit_gpu_job(notebook, data_ref, gpu=True) -> job_id   # kernels push (+ enable_gpu)
gpu_job_status(job_id) -> {state}                         # kernels status
gpu_job_output(job_id) -> minio://...                     # kernels output → MinIO
gpu_quota() -> {used, limit, window}                      # 남은 주간 GPU 시간
```

**미검증(다음 단계)**: 실제 push의 GPU 배정·쿼터 소진·산출물 경로·실행시간 상한. 이 설계는
"이 계약에 맞춰 플러그인 한다"까지만 정하고, 수치·동작은 실측으로 채운다.

## 8. 마일스톤

| M | 내용 | 완료 기준 |
|---|---|---|
| **M1** | FastMCP 뼈대 + 툴 레지스트리 + stdio + 더미 툴(`health`, `echo`) | 클라이언트에서 툴 호출 왕복, 계약 검증 |
| **M2** | GPU job 툴 (Kaggle 실측 후) + Valkey 상태·쿼터 | submit→status→output 왕복, 재시작 reconciliation |
| **M3** | 클러스터 배포(streamable-http) + kaggle-secret + 헬스체크 | in-cluster에서 툴 호출 |
| **M4** | adp-ma 프로젝트 MCP (별도 서버) | profile/start_run/run_status/run_artifact |

## 9. 배치 (모노레포)

- 코드: 최상위 `mcp-infra/` (uv 프로젝트 — adp-ma/aquarium와 동형). 단, 프로젝트 ns가 아니라
  **공유 `portfolio-infra` ns에 배포**(MinIO처럼 인프라 서비스). 매니페스트는 `.k8s/infra/mcp/`.
- 브랜치: 루트 공유 인프라이므로 `k8s/infra/<name>`.

## 10. 리스크

- **Kaggle Kernels 실동작 미검증** — 유휴 세션 문제는 없으나(배치), GPU 배정 실패·쿼터 상한·산출물
  경로는 실측 필요. 이게 M2의 선행 조건.
- **싱글톤 자원** — replica=1 강제. 향후 리더 일렉션.
- **자율 호출 부수효과** — 외부 제출 툴은 쿼터를 실제로 소모한다. 기본 비활성/확인 게이트로 방어.
- **ToS** — Kaggle Kernels는 공식 배치 API라 Colab과 달리 회색지대가 아니다(이 전환의 핵심 이유).
