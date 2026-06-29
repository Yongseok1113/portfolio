#!/usr/bin/env bash
# =============================================================================
# create-secrets.sh  —  K8s Secret 일괄 생성
#
# 실행 방법 (우선순위 순):
#   1) 자동: .k8s/.env 파일이 있으면 자동 로드 (권장)
#   2) 수동: 환경변수를 미리 export 후 실행
#      export POSTGRES_PASSWORD=xxx && ./create-secrets.sh
#   3) 인라인:
#      POSTGRES_PASSWORD=xxx MINIO_ROOT_USER=yyy ./create-secrets.sh
# =============================================================================
set -euo pipefail

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[ OK ]${NC}  $*"; }
log_error() { echo -e "${RED}[ERR ]${NC}  $*" >&2; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── .env 자동 탐색 및 로드 ────────────────────────────────────────────────────
# 스크립트 위치 기준으로 .k8s/.env 탐색
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$K8S_ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  log_info ".env 로드: $ENV_FILE"
  set -a; source "$ENV_FILE"; set +a
else
  log_warn ".env 없음 ($ENV_FILE) — 환경변수가 미리 설정되어 있어야 합니다"
fi

# ── 환경변수 확인 ─────────────────────────────────────────────────────────────
missing=()
for var in POSTGRES_PASSWORD POSTGRES_APP_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD; do
  [[ -z "${!var:-}" ]] && missing+=("$var")
done

if [[ ${#missing[@]} -gt 0 ]]; then
  log_error "아래 환경변수가 필요합니다:"
  for v in "${missing[@]}"; do
    echo "    export $v=<value>"
  done
  log_error ".k8s/.env 파일을 확인하세요: $ENV_FILE"
  exit 1
fi

# ── namespace 확인 ────────────────────────────────────────────────────────────
if ! kubectl get namespace portfolio-infra &>/dev/null; then
  log_warn "portfolio-infra namespace 없음 — 생성 중..."
  kubectl create namespace portfolio-infra
fi

# ── PostgreSQL Secret ─────────────────────────────────────────────────────────
log_info "postgres-secret 생성 중..."
kubectl create secret generic postgres-secret \
  --namespace portfolio-infra \
  --from-literal=postgres-password="$POSTGRES_PASSWORD" \
  --from-literal=password="$POSTGRES_APP_PASSWORD" \
  --from-literal=replication-password="" \
  --from-literal=POSTGRES_DB="portfolio" \
  --from-literal=POSTGRES_USER="portfolio" \
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_APP_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
log_ok "postgres-secret 완료"

# ── MinIO Secret ──────────────────────────────────────────────────────────────
log_info "minio-secret 생성 중..."
kubectl create secret generic minio-secret \
  --namespace portfolio-infra \
  --from-literal=root-user="$MINIO_ROOT_USER" \
  --from-literal=root-password="$MINIO_ROOT_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
log_ok "minio-secret 완료"

# ── 완료 ─────────────────────────────────────────────────────────────────────
echo ""
log_ok "모든 Secret 생성 완료"
echo ""
echo "확인:"
echo "  kubectl get secrets -n portfolio-infra"
echo ""
echo "다음 단계:"
echo "  .k8s/scripts/cluster-up.sh --infra-only"