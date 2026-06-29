#!/usr/bin/env bash
# =============================================================================
# portfolio/.k8s/scripts/cluster-up.sh
#
# 역할:
#   1) Minikube 클러스터 기동
#   2) 공유 인프라 배포 (portfolio-infra ns: Redis / MinIO / Ollama)
#   3) [선택] 특정 프로젝트 앱 배포  → 프로젝트/.k8s/ 내 manifest 읽음
#
# 사용법:
#   ./cluster-up.sh                          # 클러스터 + 공유 인프라만
#   ./cluster-up.sh --project adp-ma         # 인프라 + adp-ma 앱 배포
#   ./cluster-up.sh --project adp-ma \
#                   --skip-infra             # 앱만 재배포 (인프라 이미 실행 중)
#   ./cluster-up.sh --project adp-ma \
#                   --teardown project-b     # project-b 내리고 adp-ma 올리기
#   ./cluster-up.sh --infra-only             # 공유 인프라만 (앱 없음)
# =============================================================================
set -euo pipefail

# ── 색상 ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
log_info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()      { echo -e "${GREEN}[ OK ]${NC}  $*"; }
log_warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error()   { echo -e "${RED}[ERR ]${NC}  $*" >&2; }
log_section() { echo -e "\n${BLUE}━━━  $*  ━━━${NC}"; }

# ── 경로 해석 ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_ROOT="$(dirname "$SCRIPT_DIR")"        # portfolio/.k8s
PORTFOLIO_ROOT="$(dirname "$K8S_ROOT")"    # portfolio/

# ── 기본값 ───────────────────────────────────────────────────────────────────
CLUSTER_NAME="portfolio"
MINIKUBE_CPUS=8
MINIKUBE_MEMORY="14g"
MINIKUBE_DISK="60g"
MINIKUBE_DRIVER="docker"
K8S_VERSION="v1.29.0"

PROJECT=""          # --project <name>
TEARDOWN=""         # --teardown <name>
SKIP_INFRA=false    # --skip-infra
INFRA_ONLY=false    # --infra-only
SKIP_BUILD=false    # --skip-build

# ── 인수 파싱 ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)     PROJECT="$2";   shift 2 ;;
    --teardown)    TEARDOWN="$2";  shift 2 ;;
    --skip-infra)  SKIP_INFRA=true; shift ;;
    --infra-only)  INFRA_ONLY=true; shift ;;
    --skip-build)  SKIP_BUILD=true; shift ;;
    --help|-h)
      sed -n '3,20p' "$0"; exit 0 ;;
    *) log_warn "알 수 없는 옵션: $1"; shift ;;
  esac
done

# ── 의존성 확인 ───────────────────────────────────────────────────────────────
check_deps() {
  log_section "의존성 확인"
  local missing=()
  for cmd in minikube kubectl docker helm; do
    command -v "$cmd" &>/dev/null && log_ok "$cmd" || { log_error "$cmd 없음"; missing+=("$cmd"); }
  done
  [[ ${#missing[@]} -gt 0 ]] && { log_error "설치 필요: ${missing[*]}"; exit 1; }

  if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
    GPU_AVAILABLE=true
    log_ok "GPU: ${GPU_NAME}"
  else
    GPU_AVAILABLE=false
    log_warn "GPU 없음 — CPU 전용 모드"
  fi

  docker info &>/dev/null || { log_error "Docker 데몬 미실행"; exit 1; }
}

# ── 클러스터 시작 ─────────────────────────────────────────────────────────────
start_cluster() {
  log_section "Minikube 클러스터 ($CLUSTER_NAME)"

  if minikube status -p "$CLUSTER_NAME" 2>/dev/null | grep -q "Running"; then
    log_ok "이미 실행 중 — 시작 생략"
    return
  fi

  if minikube status -p "$CLUSTER_NAME" 2>/dev/null | grep -q "Stopped"; then
    log_info "재개 중..."
    minikube start -p "$CLUSTER_NAME"
    log_ok "재개 완료"
    return
  fi

  log_info "신규 생성 (CPU=${MINIKUBE_CPUS} MEM=${MINIKUBE_MEMORY} DISK=${MINIKUBE_DISK})"
  local gpu_flag=""
  [[ "$GPU_AVAILABLE" == true ]] && gpu_flag="--gpus=all"

  minikube start \
    --profile="$CLUSTER_NAME" \
    --driver="$MINIKUBE_DRIVER" \
    --cpus="$MINIKUBE_CPUS" \
    --memory="$MINIKUBE_MEMORY" \
    --disk-size="$MINIKUBE_DISK" \
    --kubernetes-version="$K8S_VERSION" \
    $gpu_flag

  minikube addons enable ingress        -p "$CLUSTER_NAME"
  minikube addons enable metrics-server -p "$CLUSTER_NAME"

  if [[ "$GPU_AVAILABLE" == true ]]; then
    log_info "nvidia-device-plugin 설치..."
    kubectl apply -f \
      https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.14.5/nvidia-device-plugin.yml
  fi
  log_ok "클러스터 생성 완료"
}

# ── kubectl context ───────────────────────────────────────────────────────────
set_context() {
  kubectl config use-context "$CLUSTER_NAME" 2>/dev/null || \
  kubectl config use-context "minikube"      2>/dev/null || true
  log_ok "context: $(kubectl config current-context)"
}

# ── Helm 레포 ─────────────────────────────────────────────────────────────────
setup_helm() {
  log_section "Helm 레포"
  # Valkey 공식 (Linux Foundation)
  helm repo add valkey https://valkey.io/valkey-helm/ --force-update 2>/dev/null || true
  # PostgreSQL — groundhog2k 경량 chart (공식 postgres 이미지 사용)
  helm repo add groundhog2k https://groundhog2k.github.io/helm-charts/ --force-update 2>/dev/null || true
  helm repo update
  log_ok "업데이트 완료"
}

# ── 네임스페이스 ─────────────────────────────────────────────────────────────
apply_namespaces() {
  log_section "네임스페이스"
  local ns_dir="$K8S_ROOT/namespaces"
  if [[ -d "$ns_dir" ]] && compgen -G "$ns_dir/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$ns_dir/"
  else
    # 최소 기본 생성
    for ns in portfolio-infra; do
      kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
    done
    log_warn "namespaces/ yaml 없음 — portfolio-infra만 생성"
  fi

  # 프로젝트 ns도 여기서 생성 (루트가 선언)
  if [[ -n "$PROJECT" ]]; then
    for ns in "${PROJECT}-system" "${PROJECT}-workers"; do
      kubectl create namespace "$ns" --dry-run=client -o yaml | kubectl apply -f -
      log_ok "ns: $ns"
    done
  fi
}

# ── 공유 인프라 배포 ──────────────────────────────────────────────────────────
deploy_infra() {
  [[ "$SKIP_INFRA" == true ]] && { log_warn "공유 인프라 생략 (--skip-infra)"; return; }
  log_section "공유 인프라 (ns: portfolio-infra)"

  # Secret 확인 — 없으면 .env 로드 후 create-secrets.sh 자동 실행
  local missing_secrets=()
  kubectl get secret postgres-secret -n portfolio-infra &>/dev/null || missing_secrets+=("postgres-secret")
  kubectl get secret minio-secret    -n portfolio-infra &>/dev/null || missing_secrets+=("minio-secret")

  if [[ ${#missing_secrets[@]} -gt 0 ]]; then
    log_warn "Secret 없음 (${missing_secrets[*]}) — create-secrets.sh 자동 실행"

    # .env 파일 로드 (있는 경우)
    local env_file="$K8S_ROOT/.env"
    if [[ -f "$env_file" ]]; then
      set -a; source "$env_file"; set +a
      log_info ".env 로드 완료: $env_file"
    else
      log_warn ".env 없음 — 환경변수가 이미 설정되어 있어야 합니다"
    fi

    # create-secrets.sh 실행
    local secrets_script="$K8S_ROOT/scripts/create-secrets.sh"
    if [[ ! -f "$secrets_script" ]]; then
      log_error "create-secrets.sh 없음: $secrets_script"
      exit 1
    fi

    bash "$secrets_script" || {
      log_error "Secret 생성 실패 — .env 파일 또는 환경변수를 확인하세요"
      log_error "  필요한 변수: POSTGRES_PASSWORD, POSTGRES_APP_PASSWORD, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD"
      exit 1
    }
  else
    log_info "Secret 확인 완료 (postgres-secret, minio-secret)"
  fi

  # Valkey (Redis 대체 — Linux Foundation 공식 오픈소스)
  log_info "Valkey..."
  local valkey_values="$K8S_ROOT/infra/redis/values.yaml"
  if [[ -f "$valkey_values" ]]; then
    helm upgrade --install portfolio-valkey valkey/valkey \
      --namespace portfolio-infra --values "$valkey_values" \
      --wait --timeout 3m
  else
    helm upgrade --install portfolio-valkey valkey/valkey \
      --namespace portfolio-infra \
      --set replica.enabled=false \
      --set auth.enabled=false \
      --set persistence.size=2Gi \
      --wait --timeout 3m
  fi
  log_ok "Valkey 완료"

  # PostgreSQL 16 (groundhog2k chart + 공식 postgres 이미지)
  log_info "PostgreSQL..."
  local pg_values="$K8S_ROOT/infra/postgres/values.yaml"
  if [[ -f "$pg_values" ]]; then
    helm upgrade --install portfolio-postgres groundhog2k/postgres \
      --namespace portfolio-infra --values "$pg_values" \
      --wait --timeout 5m
  else
    helm upgrade --install portfolio-postgres groundhog2k/postgres \
      --namespace portfolio-infra \
      --set image.tag=16.6 \
      --set settings.existingSecret=postgres-secret \
      --set settings.superuserPasswordKey=postgres-password \
      --set database.name=portfolio \
      --set database.user=portfolio \
      --set database.userPasswordKey=password \
      --set storage.persistentVolumeClaim.size=5Gi \
      --wait --timeout 5m
  fi
  log_ok "PostgreSQL 완료"

  # MinIO (공식 이미지 — Bitnami 유료 전환 대응)
  log_info "MinIO..."
  local minio_dir="$K8S_ROOT/infra/minio"
  if compgen -G "$minio_dir/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$minio_dir/"
    log_ok "MinIO 완료"
  else
    log_warn "infra/minio/*.yaml 없음 — MinIO 생략"
  fi

  # Ollama (GPU)
  log_info "Ollama..."
  local ollama_dir="$K8S_ROOT/infra/ollama"
  if compgen -G "$ollama_dir/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$ollama_dir/"
    log_ok "Ollama 완료"
  else
    log_warn "infra/ollama/*.yaml 없음 — Ollama 생략"
  fi

  # 공유 ConfigMap
  local cm_dir="$K8S_ROOT/config"
  if compgen -G "$cm_dir/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$cm_dir/"
    log_ok "공유 ConfigMap 완료"
  fi
}

# ── 프로젝트 teardown ────────────────────────────────────────────────────────
teardown_project() {
  local proj="$1"
  log_section "Teardown: $proj"
  local proj_k8s="$PORTFOLIO_ROOT/$proj/.k8s"

  if [[ ! -d "$proj_k8s" ]]; then
    log_warn "$proj/.k8s 없음 — teardown 생략"
    return
  fi

  # 역순 삭제 (web-ui → meta-agents → workers → config → rbac)
  for subdir in web-ui meta-agents workers config rbac; do
    local d="$proj_k8s/$subdir"
    if compgen -G "$d/*.yaml" > /dev/null 2>&1; then
      kubectl delete -f "$d/" --namespace "${proj}-system" --ignore-not-found 2>/dev/null || true
    fi
  done

  # workers ns Job 정리
  kubectl delete jobs --all --namespace "${proj}-workers" --ignore-not-found 2>/dev/null || true
  log_ok "$proj teardown 완료"
}

# ── 프로젝트 이미지 빌드 ──────────────────────────────────────────────────────
build_project_images() {
  local proj="$1"
  [[ "$SKIP_BUILD" == true ]] && { log_warn "이미지 빌드 생략 (--skip-build)"; return; }

  log_section "이미지 빌드: $proj"
  eval "$(minikube -p "$CLUSTER_NAME" docker-env)"

  local src_dir="$PORTFOLIO_ROOT/$proj/src"
  local built=0
  if [[ -d "$src_dir" ]]; then
    while IFS= read -r -d '' dockerfile; do
      local component
      component="$(basename "$(dirname "$dockerfile")")"
      log_info "$component 빌드 중..."
      docker build -t "${proj}/${component}:local" \
        -f "$dockerfile" "$(dirname "$dockerfile")"
      log_ok "${proj}/${component}:local"
      built=$((built+1))
    done < <(find "$src_dir" -name "Dockerfile" -print0)
  fi
  [[ $built -eq 0 ]] && log_warn "Dockerfile 없음 — 빌드 생략"

  eval "$(minikube -p "$CLUSTER_NAME" docker-env --unset)"
}

# ── 프로젝트 앱 배포 ──────────────────────────────────────────────────────────
deploy_project() {
  local proj="$1"
  log_section "앱 배포: $proj (ns: ${proj}-system)"

  local proj_k8s="$PORTFOLIO_ROOT/$proj/.k8s"
  if [[ ! -d "$proj_k8s" ]]; then
    log_error "$proj/.k8s 디렉토리 없음"
    log_error "경로 확인: $proj_k8s"
    exit 1
  fi

  # RBAC 공통 적용 (루트)
  if compgen -G "$K8S_ROOT/rbac/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$K8S_ROOT/rbac/" --namespace "${proj}-system"
    log_ok "공통 RBAC 적용"
  fi

  # 프로젝트 자체 RBAC
  local rbac_dir="$proj_k8s/rbac"
  if compgen -G "$rbac_dir/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$rbac_dir/" --namespace "${proj}-system"
    log_ok "프로젝트 RBAC 적용"
  fi

  # ConfigMap
  local config_dir="$proj_k8s/config"
  if compgen -G "$config_dir/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$config_dir/" --namespace "${proj}-system"
    log_ok "ConfigMap 적용"
  fi

  # 앱 배포 (순서 보장: meta-agents → web-ui, workers는 런타임 생성)
  for subdir in meta-agents web-ui; do
    local d="$proj_k8s/$subdir"
    if compgen -G "$d/**/*.yaml" > /dev/null 2>&1 || \
       compgen -G "$d/*.yaml"   > /dev/null 2>&1; then
      kubectl apply -f "$d/" --namespace "${proj}-system" --recursive
      log_ok "$subdir 적용"
    else
      log_warn "$subdir manifest 없음 — 생략"
    fi
  done

  # workers namespace용 Job 템플릿 (ns: project-workers)
  local workers_dir="$proj_k8s/workers"
  if compgen -G "$workers_dir/*.yaml" > /dev/null 2>&1; then
    kubectl apply -f "$workers_dir/" --namespace "${proj}-workers"
    log_ok "workers 템플릿 적용"
  fi

  # Rollout 대기
  log_info "Rollout 대기 (최대 5분)..."
  kubectl get deployments -n "${proj}-system" \
    --no-headers -o custom-columns="NAME:.metadata.name" 2>/dev/null | \
  while read -r dep; do
    kubectl rollout status deployment/"$dep" \
      -n "${proj}-system" --timeout=5m 2>/dev/null && \
      log_ok "$dep 준비 완료" || \
      log_warn "$dep rollout 미완료"
  done
}

# ── 완료 요약 ─────────────────────────────────────────────────────────────────
print_summary() {
  log_section "배포 완료"
  echo ""
  echo -e "${GREEN}공유 인프라 (portfolio-infra):${NC}"
  kubectl get pods -n portfolio-infra 2>/dev/null | sed 's/^/  /' || true

  if [[ -n "$PROJECT" ]]; then
    echo ""
    echo -e "${GREEN}앱 Pod (${PROJECT}-system):${NC}"
    kubectl get pods -n "${PROJECT}-system" 2>/dev/null | sed 's/^/  /' || true
  fi

  local mk_ip
  mk_ip=$(minikube ip -p "$CLUSTER_NAME" 2>/dev/null || echo "N/A")

  echo ""
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "  Minikube IP  : ${mk_ip}"
  echo -e "  Valkey       : kubectl port-forward svc/portfolio-valkey 6379:6379 -n portfolio-infra"
  echo -e "  PostgreSQL   : kubectl port-forward svc/portfolio-postgres-primary 5432:5432 -n portfolio-infra"
  echo -e "  MinIO 콘솔   : kubectl port-forward svc/minio 9001:9001 -n portfolio-infra"
  echo -e "  Ollama       : kubectl port-forward svc/ollama 11434:11434 -n portfolio-infra"
  if [[ -n "$PROJECT" ]]; then
    echo -e "  Web UI       : kubectl port-forward svc/${PROJECT}-webui 8080:80 -n ${PROJECT}-system"
  fi
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# ── 메인 ─────────────────────────────────────────────────────────────────────
main() {
  echo -e "${BLUE}"
  echo "╔═══════════════════════════════════════════╗"
  echo "║   portfolio  ·  cluster-up.sh             ║"
  [[ -n "$PROJECT" ]]  && printf "║   project : %-30s║\n" "$PROJECT"
  [[ -n "$TEARDOWN" ]] && printf "║   teardown: %-30s║\n" "$TEARDOWN"
  echo "╚═══════════════════════════════════════════╝"
  echo -e "${NC}"

  check_deps
  start_cluster
  set_context
  setup_helm
  apply_namespaces

  # 기존 프로젝트 내리기
  [[ -n "$TEARDOWN" ]] && teardown_project "$TEARDOWN"

  # 공유 인프라
  [[ "$INFRA_ONLY" == false ]] && deploy_infra || { deploy_infra; print_summary; exit 0; }

  # 프로젝트 배포
  if [[ -n "$PROJECT" ]]; then
    build_project_images "$PROJECT"
    deploy_project "$PROJECT"
  fi

  print_summary
}

main "$@"