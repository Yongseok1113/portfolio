#!/usr/bin/env bash
# =============================================================================
# adp-ma/.k8s/scripts/setup.sh — adp-ma K8s 실행 준비
#
#   1) minio-secret을 portfolio-infra → adp-ma-workers 복제 (ground agent Job용)
#   2) controller 준비 (③): minio/groq secret·infra-endpoints를 adp-ma-system으로,
#      controller RBAC 적용
#   3) worker/controller 공용 이미지 빌드 (minikube 내부)
#
# 전제: 루트 cluster-up.sh 완료 (클러스터·네임스페이스·시크릿 존재)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(dirname "$SCRIPT_DIR")"          # adp-ma/.k8s
PROJECT_DIR="$(dirname "$K8S_DIR")"         # adp-ma/
CLUSTER_NAME="portfolio"
IMAGE_TAG="adp-ma:0.1.0"

# portfolio-infra의 리소스를 대상 ns로 복제 (ns 고유 메타 제거)
copy_to() {  # copy_to <kind> <name> <target-ns>
  kubectl get "$1" "$2" -n portfolio-infra -o yaml \
    | grep -Ev '^\s*(namespace|uid|resourceVersion|creationTimestamp):' \
    | kubectl apply -n "$3" -f -
}

echo "[1/3] minio-secret 복제 → adp-ma-workers"
copy_to secret minio-secret adp-ma-workers

echo "[2/3] controller 준비 → adp-ma-system (secret·configmap 복제 + RBAC)"
copy_to secret    minio-secret    adp-ma-system
copy_to secret    groq-secret     adp-ma-system
copy_to configmap infra-endpoints adp-ma-system
kubectl apply -f "$K8S_DIR/rbac/controller-rbac.yaml"

echo "[3/3] 이미지 빌드: $IMAGE_TAG"
minikube -p "$CLUSTER_NAME" image build -t "$IMAGE_TAG" "$PROJECT_DIR"

cat <<'EOF'
완료.

로컬 CLI에서 K8s executor 실행:
  kubectl port-forward svc/minio 9000:9000 -n portfolio-infra &
  EXECUTOR=k8s MINIO_ENDPOINT=http://127.0.0.1:9000 \
    uv run adp-ma run -i examples/sales_raw.csv -g "..." --archive

in-cluster controller 실행 (파이프라인 전체를 클러스터 Job으로):
  # 입력을 MinIO에 올린 뒤 (예: inputs/sales_raw.csv), 아래로 실행
  sed 's|<INPUT_URI>|minio://adp-ma/inputs/sales_raw.csv|; s|<GOAL>|중복 제거·집계|' \
    .k8s/controller/controller-job.yaml | kubectl create -f -
EOF
