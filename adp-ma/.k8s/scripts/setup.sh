#!/usr/bin/env bash
# =============================================================================
# adp-ma/.k8s/scripts/setup.sh — adp-ma K8s Job 실행 준비
#
#   1) minio-secret을 portfolio-infra → adp-ma-workers로 복제
#   2) controller RBAC 적용
#   3) worker 이미지 빌드 (minikube 내부 빌드)
#
# 전제: 루트 cluster-up.sh 완료 (클러스터·네임스페이스·시크릿 존재)
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K8S_DIR="$(dirname "$SCRIPT_DIR")"          # adp-ma/.k8s
PROJECT_DIR="$(dirname "$K8S_DIR")"         # adp-ma/
CLUSTER_NAME="portfolio"
IMAGE_TAG="adp-ma:0.1.0"

echo "[1/3] minio-secret 복제 → adp-ma-workers"
kubectl get secret minio-secret -n portfolio-infra -o yaml \
  | grep -Ev '^\s*(namespace|uid|resourceVersion|creationTimestamp):' \
  | kubectl apply -n adp-ma-workers -f -

echo "[2/3] controller RBAC 적용"
kubectl apply -f "$K8S_DIR/rbac/controller-rbac.yaml"

echo "[3/3] worker 이미지 빌드: $IMAGE_TAG"
minikube -p "$CLUSTER_NAME" image build -t "$IMAGE_TAG" "$PROJECT_DIR"

echo "완료. 로컬에서 K8s executor로 실행하려면:"
echo "  kubectl port-forward svc/minio 9000:9000 -n portfolio-infra &"
echo "  ADP_EXECUTOR=k8s 대신 .env에 EXECUTOR=k8s MINIO_ENDPOINT=http://127.0.0.1:9000 설정"
echo "  (MINIO_ROOT_USER/PASSWORD는 루트 .k8s/.env와 동일)"
