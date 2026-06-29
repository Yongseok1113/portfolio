
# 포트폴리오



디렉토리 구조

```
portfolio/                  
├── .k8s/                      
│   ├── scripts/
│   │   ├── cluster-up.sh
│   │   ├── cluster-down.sh
│   │   ├── cluster-clean.sh
│   │   └── cluster-status.sh
│   ├── namespaces/
│   │   ├── portfolio-infra.yaml
│   │   ├── adp-ma.yaml
│   │   └── _template.yaml
│   ├── infra/
│   │   ├── redis/values.yaml
│   │   ├── minio/values.yaml
│   │   └── ollama/deployment.yaml, svc.yaml
│   ├── rbac/
│   │   └── project-ns-grant.yaml
│   └── config/
│       └── infra-endpoints.yaml
│
├── Aquarium/                    
│   ├── .k8s/
│   │   ├── meta-agents/
│   │   │   ├── orchestrator/
│   │   │   ├── architect/
│   │   │   └── monitor/
│   │   ├── workers/
│   │   │   └── ground-agent-job-template.yaml
│   │   ├── config/
│   │   │   └── app-configmap.yaml
│   │   ├── rbac/
│   │   │   ├── architect-sa.yaml
│   │   │   └── role-workers.yaml
│   │   └── web-ui/
│   │       ├── deployment.yaml
│   │       ├── svc.yaml
│   │       └── ingress.yaml
│   ├── src/
│   │   ├── orchestrator/
│   │   ├── architect/
│   │   ├── monitor/
│   │   └── web-ui/
│   ├── pyproject.toml
│   └── README.md
│
├── project-b/                 ← 추후 추가 (동일 패턴)
│   └── .k8s/
│
└── README.md                  ← 포트폴리오 전체 소개