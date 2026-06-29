#!/usr/bin/env bash
# =============================================================================
# setup-branch-protection.sh
#
# portfolio 레포 브랜치 보호 규칙 설정
#
# 사전 조건:
#   gh auth login  (GitHub CLI 인증)
#
# 사용법:
#   ./setup-branch-protection.sh <github-username> <repo-name>
#   ./setup-branch-protection.sh Yongseok1113 portfolio
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
log_info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
log_ok()   { echo -e "${GREEN}[ OK ]${NC}  $*"; }
log_error(){ echo -e "${RED}[ERR ]${NC}  $*" >&2; }

# ── 인수 확인 ─────────────────────────────────────────────────────────────────
if [[ $# -lt 2 ]]; then
  echo "사용법: $0 <github-username> <repo-name>"
  echo "예시 : $0 ysoh1113 portfolio"
  exit 1
fi

OWNER="$1"
REPO="$2"

# ── gh CLI 확인 ───────────────────────────────────────────────────────────────
if ! command -v gh &>/dev/null; then
  log_error "gh CLI가 설치되어 있지 않습니다."
  log_error "설치: https://cli.github.com"
  exit 1
fi

if ! gh auth status &>/dev/null; then
  log_error "GitHub 인증이 필요합니다: gh auth login"
  exit 1
fi

log_info "레포: ${OWNER}/${REPO}"

# ── 보호 규칙 payload ─────────────────────────────────────────────────────────
# 실무 협업 기준:
#   - PR 필수 (직접 push 차단 — 관리자 포함)
#   - 승인 카운트 0 (개인 레포, 혼자 머지 가능)
#   - force push 금지
#   - 브랜치 삭제 금지
#   - enforce_admins true → owner도 규칙 적용
PROTECTION_PAYLOAD='{
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": false
  },
  "enforce_admins": true,
  "restrictions": null,
  "required_status_checks": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}'

# ── 브랜치 보호 적용 함수 ──────────────────────────────────────────────────────
protect_branch() {
  local branch="$1"
  # 슬래시를 %2F로 인코딩
  local encoded="${branch//\//%2F}"

  log_info "보호 설정 중: $branch"

  # 브랜치 존재 확인
  if ! gh api "repos/${OWNER}/${REPO}/branches/${encoded}" &>/dev/null; then
    log_error "브랜치 없음: $branch (먼저 push 후 실행하세요)"
    return 1
  fi

  gh api \
    --method PUT \
    "repos/${OWNER}/${REPO}/branches/${encoded}/protection" \
    --input - <<< "$PROTECTION_PAYLOAD" > /dev/null

  log_ok "$branch 보호 설정 완료"
}

# ── 메인 ─────────────────────────────────────────────────────────────────────
echo ""
echo "브랜치 보호 규칙:"
echo "  - PR 필수 (직접 push 차단 — 관리자 포함)"
echo "  - 승인 카운트: 0 (개인 레포)"
echo "  - force push: 금지"
echo "  - 브랜치 삭제: 금지"
echo "  - enforce_admins: true (owner도 규칙 적용)"
echo ""

protect_branch "main"
protect_branch "develop/adp-ma"
protect_branch "develop/aquarium"

echo ""
log_ok "완료"
echo ""
echo "확인: https://github.com/${OWNER}/${REPO}/settings/branches"