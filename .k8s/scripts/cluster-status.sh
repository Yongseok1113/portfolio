#!/usr/bin/env bash
# =============================================================================
# portfolio/.k8s/scripts/cluster-status.sh
#
# 사용법:
#   ./cluster-status.sh                  # 전체 상태
#   ./cluster-status.sh --watch          # 5초 갱신
#   ./cluster-status.sh --project adp-ma # 특정 프로젝트 집중
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
CYAN='\033[0;36m'; GRAY='\033[0;90m'; NC='\033[0m'

CLUSTER_NAME="portfolio"
WATCH=false
PROJECT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --watch)   WATCH=true; shift ;;
    --project) PROJECT="$2"; shift 2 ;;
    *) shift ;;
  esac
done

print_status() {
  clear 2>/dev/null || true
  echo -e "${BLUE}"
  printf "╔══════════════════════════════════════════════════════╗\n"
  printf "║  portfolio 클러스터 상태   %-25s║\n" "$(date '+%Y-%m-%d %H:%M:%S')"
  printf "╚══════════════════════════════════════════════════════╝\n"
  echo -e "${NC}"

  # 클러스터
  echo -e "${CYAN}▸ Minikube${NC}"
  if ! minikube status -p "$CLUSTER_NAME" &>/dev/null; then
    echo -e "  ${YELLOW}클러스터 없음${NC}  →  ./cluster-up.sh --project <name>"
    return
  fi
  minikube status -p "$CLUSTER_NAME" 2>/dev/null | sed 's/^/  /'

  # GPU
  local gpu
  gpu=$(kubectl describe node 2>/dev/null | grep "nvidia.com/gpu:" | awk '{print $2}' | head -1)
  [[ -n "$gpu" ]] && echo -e "  ${GREEN}GPU: nvidia.com/gpu = $gpu${NC}"

  # 공유 인프라
  echo -e "\n${CYAN}▸ 공유 인프라 (portfolio-infra)${NC}"
  kubectl get pods -n portfolio-infra --no-headers 2>/dev/null | \
    awk '{
      status=$3; color="\033[0;32m"
      if(status!="Running" && status!="Completed") color="\033[1;33m"
      printf "  " color "%s\033[0m  %s  %s\n", $1, $3, $5
    }' || echo -e "  ${GRAY}(없음)${NC}"

  # Helm
  echo -e "\n${CYAN}▸ Helm 릴리즈${NC}"
  helm list -n portfolio-infra 2>/dev/null | sed 's/^/  /' || echo -e "  ${GRAY}(없음)${NC}"

  # 프로젝트 목록 자동 탐색
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  PORTFOLIO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

  local projects=()
  [[ -n "$PROJECT" ]] && projects=("$PROJECT") || {
    while IFS= read -r -d '' d; do
      projects+=("$(basename "$(dirname "$d")")")
    done < <(find "$PORTFOLIO_ROOT" -maxdepth 2 -name ".k8s" -not -path "*/.k8s/.k8s" -print0 2>/dev/null)
    # portfolio/.k8s 자신은 제외
    local filtered=()
    for p in "${projects[@]}"; do
      [[ "$p" != "$(basename "$PORTFOLIO_ROOT")" ]] && filtered+=("$p")
    done
    projects=("${filtered[@]}")
  }

  for proj in "${projects[@]}"; do
    local sys_ns="${proj}-system"
    local wrk_ns="${proj}-workers"

    if kubectl get ns "$sys_ns" &>/dev/null; then
      echo -e "\n${CYAN}▸ 프로젝트: $proj${NC}"
      echo -e "  ${GRAY}(${sys_ns})${NC}"
      kubectl get pods -n "$sys_ns" --no-headers 2>/dev/null | \
        awk '{
          status=$3; color="\033[0;32m"
          if(status!="Running") color="\033[1;33m"
          printf "  " color "%s\033[0m  %s\n", $1, $3
        }' || echo -e "    ${GRAY}(Pod 없음)${NC}"

      local job_count
      job_count=$(kubectl get jobs -n "$wrk_ns" --no-headers 2>/dev/null | wc -l)
      echo -e "  ${GRAY}(${wrk_ns}) — Jobs: ${job_count}개${NC}"
    fi
  done

  # PVC
  echo -e "\n${CYAN}▸ PVC (portfolio-infra)${NC}"
  kubectl get pvc -n portfolio-infra --no-headers 2>/dev/null | \
    awk '{printf "  %s  %s  %s\n", $1, $2, $4}' || echo -e "  ${GRAY}(없음)${NC}"

  # 접근 URL
  local mk_ip
  mk_ip=$(minikube ip -p "$CLUSTER_NAME" 2>/dev/null || echo "N/A")
  echo ""
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "  Minikube IP : ${mk_ip}"
  echo -e "  Redis       : kubectl port-forward svc/portfolio-redis-master 6379:6379 -n portfolio-infra"
  echo -e "  MinIO       : kubectl port-forward svc/portfolio-minio-console 9001:9001 -n portfolio-infra"
  echo -e "  Ollama      : kubectl port-forward svc/ollama 11434:11434 -n portfolio-infra"
  [[ -n "$PROJECT" ]] && \
    echo -e "  Web UI      : kubectl port-forward svc/${PROJECT}-webui 8080:80 -n ${PROJECT}-system"
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  [[ "$WATCH" == true ]] && echo -e "${GRAY}  5초마다 갱신 — Ctrl+C 종료${NC}"
}

if [[ "$WATCH" == true ]]; then
  while true; do print_status; sleep 5; done
else
  print_status
fi