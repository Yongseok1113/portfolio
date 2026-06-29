# Git 개발 규칙

## 브랜치 구조

```
main
└── develop/adp-ma          ← project-a 통합 브랜치
    ├── feat/adp-ma/<name>  ← 기능 개발
    ├── fix/adp-ma/<name>   ← 버그 수정
    └── k8s/adp-ma/<name>   ← K8s manifest 변경

k8s/infra/<name>            ← 루트 공유 인프라 변경 (독립)
```

---

## 브랜치별 역할

| 브랜치 | 보호 | 역할 | 직접 push |
|---|---|---|---|
| `main` | O | 릴리즈. 태그 기준점 | 금지 |
| `develop/adp-ma` | O | project-a 통합. PR만 머지 | 금지 |
| `feat/adp-ma/*` | X | 기능 단위 개발 | 허용 |
| `fix/adp-ma/*` | X | 버그 수정 | 허용 |
| `k8s/adp-ma/*` | X | 프로젝트 K8s manifest | 허용 |
| `k8s/infra/*` | X | 루트 공유 인프라 manifest | 허용 |

---

## 네이밍 규칙

```bash
# 기능
feat/adp-ma/orchestrator
feat/adp-ma/architect-job-gen
feat/adp-ma/monitor-backtrack
feat/adp-ma/web-ui-dashboard

# 버그 수정
fix/adp-ma/redis-connection
fix/adp-ma/gpu-oom

# 프로젝트 K8s
k8s/adp-ma/meta-agent-deploy
k8s/adp-ma/ollama-config

# 루트 공유 인프라
k8s/infra/ollama-gpu-setup
k8s/infra/redis-values
```

- 소문자 + 하이픈만 사용
- 동사 또는 명사구로 간결하게

---

## 머지 흐름

```
feat/adp-ma/x  ──┐
fix/adp-ma/x   ──┤  PR (Squash merge)  →  develop/adp-ma
k8s/adp-ma/x   ──┘

develop/adp-ma  ──  PR (Merge commit)  →  main  +  tag

k8s/infra/x  ──  PR (Squash merge)  →  main
```

### Squash merge를 쓰는 이유

작업 브랜치의 WIP 커밋들을 하나로 압축해서 `develop/adp-ma` 히스토리를 깔끔하게 유지합니다.

---

## 커밋 메시지 규칙

```
<type>(<scope>): <요약>

[본문 — 선택]
```

### type

| type | 사용 시점 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `k8s` | K8s manifest 추가·수정 |
| `refactor` | 동작 변경 없는 코드 정리 |
| `docs` | 문서 |
| `chore` | 빌드·의존성·설정 |

### scope

| scope | 대상 |
|---|---|
| `orchestrator` | Orchestrator 에이전트 |
| `architect` | Architect 에이전트 |
| `monitor` | Monitor 에이전트 |
| `worker` | Ground Agent Job |
| `web-ui` | Web UI |
| `infra` | 공유 인프라 (루트 .k8s) |
| `root` | 루트 레벨 공통 |

### 예시

```
feat(orchestrator): 파이프라인 플랜 생성 API 구현

fix(architect): GPU Job 스케줄링 실패 시 재시도 로직 추가

k8s(infra): Ollama GPU 리소스 limit 설정

k8s(architect): architect-sa RBAC adp-ma-workers 권한 추가

chore(root): uv 의존성 업데이트
```

---

## 태그 규칙

`main` 머지 시 태그를 함께 생성합니다.

```bash
# project-a 마일스톤
v0.1.0-adp-ma   # 첫 데모 (메타에이전트 3종 동작)
v0.2.0-adp-ma   # Ground Agent 동적 생성 동작
v1.0.0-adp-ma   # 포트폴리오 완성본

# 포트폴리오 전체
v1.0.0          # 모든 프로젝트 완성
```

```bash
# 태그 생성 + push
git tag -a v0.1.0-adp-ma -m "adp-ma: 메타에이전트 3종 초기 배포"
git push origin v0.1.0-adp-ma
```

---

## 작업 흐름 요약

```bash
# 1. 작업 시작 — develop/adp-ma 기준으로 브랜치 생성
git switch develop/adp-ma
git pull origin develop/adp-ma
git switch -c feat/adp-ma/orchestrator

# 2. 개발 + 커밋
git add .
git commit -m "feat(orchestrator): 플랜 생성 초기 구현"

# 3. push
git push origin feat/adp-ma/orchestrator

# 4. PR: feat/adp-ma/orchestrator → develop/adp-ma (Squash merge)

# 5. 데모 완성 시 PR: develop/adp-ma → main (Merge commit + tag)
```

---

## 새 프로젝트 추가 시

```bash
# 브랜치
develop/project-b
feat/project-b/<name>
fix/project-b/<name>
k8s/project-b/<name>

# 네임스페이스 yaml
portfolio/.k8s/namespaces/project-b.yaml  ← 루트가 선언

# 앱 manifest
portfolio/project-b/.k8s/               ← 프로젝트가 소유
```