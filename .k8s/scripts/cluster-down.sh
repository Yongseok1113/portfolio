#!/usr/bin/env bash
# =============================================================================
# portfolio/.k8s/scripts/cluster-down.sh
#
# 사용법:
#   ./cluster-down.sh                    # 클러스터 일시 중지 (PV 보존)
#   ./cluster-down.sh --project adp-ma  # 특정 프로젝트 앱만 내리기
#   ./cluster-down.sh --all-projects    # 모든 프로젝트 앱 내리기 (인프라 유지)
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
log_info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()   { echo -e "${GREEN}[ OK ]${NC}  $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTFOLIO_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
CLUSTER_NAME="portfolio"
PROJECT=""
ALL_PROJECTS=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)      PROJECT="$2"; shift 2 ;;
    --all-projects) ALL_PROJECTS=true; shift ;;
    --help|-h) sed -n '3,10p' "$0"; exit 0 ;;
    *) log_warn "알 수 없는 옵션: $1"; shift ;;
  esac
done

echo -e "${BLUE}"
echo "╔═══════════════════════════════════════════╗"
echo "║   portfolio  ·  cluster-down.sh           ║"
echo "╚═══════════════════════════════════════════╝"
echo -e "${NC}"

minikube status -p "$CLUSTER_NAME" &>/dev/null || { log_warn "클러스터 없음"; exit 0; }

# ── 특정 프로젝트 앱만 내리기 ────────────────────────────────────────────────
teardown_project() {
  local proj="$1"
  log_info "[$proj] 앱 리소스 삭제 중..."
  local proj_k8s="$PORTFOLIO_ROOT/$proj/.k8s"

  if [[ ! -d "$proj_k8s" ]]; then
    log_warn "$proj/.k8s 없음"
    return
  fi

  for subdir in web-ui meta-agents workers config rbac; do
    local d="$proj_k8s/$subdir"
    compgen -G "$d/*.yaml" > /dev/null 2>&1 && \
      kubectl delete -f "$d/" \
        --namespace "${proj}-system" \
        --ignore-not-found 2>/dev/null || true
  done
  kubectl delete jobs --all \
    --namespace "${proj}-workers" \
    --ignore-not-found 2>/dev/null || true

  log_ok "[$proj] 앱 삭제 완료 (인프라·PV 보존)"
}

# 프로젝트 지정 시 → 앱만 삭제, 클러스터 유지
if [[ -n "$PROJECT" ]]; then
  teardown_project "$PROJECT"
  exit 0
fi

# 전체 프로젝트 앱 삭제 → 클러스터 유지
if [[ "$ALL_PROJECTS" == true ]]; then
  for proj_dir in "$PORTFOLIO_ROOT"/*/; do
    proj="$(basename "$proj_dir")"
    [[ -d "$proj_dir/.k8s" ]] && teardown_project "$proj" || true
  done
  log_ok "모든 프로젝트 앱 삭제 완료 (인프라·클러스터 유지)"
  exit 0
fi

# 기본: 클러스터 일시 중지
pkill -f "kubectl port-forward" 2>/dev/null && log_ok "포트포워딩 종료" || true

log_info "클러스터 중지 중 (PV 데이터 보존)..."
minikube stop -p "$CLUSTER_NAME"
log_ok "클러스터 중지 완료"
echo ""
echo -e "  재시작: ${CYAN}./cluster-up.sh --project <name>${NC}"
echo -e "  완전삭제: ${CYAN}./cluster-clean.sh${NC}"