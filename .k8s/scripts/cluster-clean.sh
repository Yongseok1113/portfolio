#!/usr/bin/env bash
# =============================================================================
# portfolio/.k8s/scripts/cluster-clean.sh
#
# 사용법:
#   ./cluster-clean.sh           # 확인 후 전체 삭제
#   ./cluster-clean.sh --force   # 확인 없이 즉시 삭제
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
log_info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()   { echo -e "${GREEN}[ OK ]${NC}  $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

CLUSTER_NAME="portfolio"
FORCE=false

[[ "${1:-}" == "--force" ]] && FORCE=true

echo -e "${RED}"
echo "╔═══════════════════════════════════════════╗"
echo "║   portfolio  ·  cluster-clean.sh          ║"
echo "║   ⚠  모든 데이터 삭제                      ║"
echo "╚═══════════════════════════════════════════╝"
echo -e "${NC}"

if [[ "$FORCE" == false ]]; then
  echo -e "${RED}경고: PV 포함 클러스터 전체가 삭제됩니다.${NC}"
  read -r -p "계속하시겠습니까? (yes/no): " confirm
  [[ "$confirm" != "yes" ]] && { log_warn "취소"; exit 0; }
fi

# Helm 릴리즈 삭제
if command -v helm &>/dev/null && minikube status -p "$CLUSTER_NAME" &>/dev/null; then
  kubectl config use-context "$CLUSTER_NAME" 2>/dev/null || \
  kubectl config use-context "minikube"      2>/dev/null || true

  for release in portfolio-redis portfolio-minio; do
    helm status "$release" -n portfolio-infra &>/dev/null && \
      helm uninstall "$release" -n portfolio-infra --wait 2>/dev/null && \
      log_ok "$release 삭제" || true
  done
fi

log_info "클러스터 삭제 중..."
minikube delete -p "$CLUSTER_NAME" --purge 2>/dev/null && log_ok "클러스터 삭제" || true

# kubectl context 정리
kubectl config delete-context "$CLUSTER_NAME" 2>/dev/null || true
kubectl config delete-cluster  "$CLUSTER_NAME" 2>/dev/null || true
log_ok "context 정리 완료"
echo ""
log_ok "완료. 새로 시작: ./cluster-up.sh --project <name>"