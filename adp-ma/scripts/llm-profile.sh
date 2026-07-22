#!/usr/bin/env bash
# LLM 백엔드 전환 헬퍼 — adp-ma/.env 의 LLM_* 블록을 로컬(Ollama)/Groq로 스위치한다.
#
#   scripts/llm-profile.sh local [모델]   # 기본 모델: qwen2.5-coder:7b
#   scripts/llm-profile.sh groq           # LLM_* 별칭을 비워 Groq(기본값)로 복귀
#   scripts/llm-profile.sh status         # 현재 설정 출력
#
# 원리: config.py 는 LLM_* 가 있으면 GROQ_* 를 덮어쓴다(LLM_* > GROQ_*).
# 따라서 로컬 전환은 LLM_* 세 줄을 켜고, Groq 복귀는 그 줄을 비우면 된다.
# GROQ_API_KEY 등 기존 값은 건드리지 않아 Groq 자격증명이 보존된다.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV=".env"
# 로컬 Ollama는 Groq 키가 필요 없어 adp-ma/.env 가 없을 수 있다 → 없으면 생성
[ -f "$ENV" ] || { touch "$ENV"; echo "($ENV 생성)"; }

# LLM_* 로 시작하는 기존 줄을 제거한 사본을 만든다 (멱등)
strip() { grep -vE '^(LLM_BASE_URL|LLM_MODEL|LLM_API_KEY)=' "$ENV" > "$ENV.tmp" || true; mv "$ENV.tmp" "$ENV"; }

case "${1:-status}" in
  local)
    model="${2:-qwen2.5-coder:7b}"
    base="${OLLAMA_HOST:-http://localhost:11434}/v1"
    strip
    { echo "LLM_BASE_URL=$base"; echo "LLM_MODEL=$model"; echo "LLM_API_KEY=local"; } >> "$ENV"
    echo "→ 로컬(Ollama) 전환: $model @ $base"
    ;;
  groq)
    strip
    echo "→ Groq 복귀: LLM_* 별칭 제거 (GROQ_* 값 사용)"
    ;;
  status)
    echo "현재 .env LLM 설정:"
    grep -E '^(LLM_|GROQ_)(BASE_URL|MODEL|API_KEY)=' "$ENV" | sed -E 's/(API_KEY=).*/\1***/' || echo "  (LLM_* 없음 → Groq 기본값)"
    ;;
  *)
    echo "usage: $0 {local [model]|groq|status}"; exit 2 ;;
esac
