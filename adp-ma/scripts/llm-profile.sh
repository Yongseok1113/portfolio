#!/usr/bin/env bash
# LLM 백엔드 전환 헬퍼 — adp-ma/.env 의 LLM_* 블록을 로컬(Ollama)/Groq로 스위치한다.
#
#   scripts/llm-profile.sh local [모델] [경량모델]  # 기본 모델: qwen2.5-coder:7b
#   scripts/llm-profile.sh groq                     # LLM_* 별칭을 비워 Groq(기본값)로 복귀
#   scripts/llm-profile.sh light <모델>|off         # 역할별 라우팅의 경량 모델만 지정/해제
#   scripts/llm-profile.sh status                   # 현재 설정 출력
#
# 역할별 라우팅: 경량 모델을 지정하면 저위험·고토큰 역할(요약·EDA 서술·도구선택·
# 게이트 테스트)만 그 모델로 가고, 계획·확장·코드생성·수정은 주 모델을 쓴다.
# Groq에서는 20b와 70b의 TPD 쿼터가 분리돼 있어 주 모델 쿼터를 아끼는 효과가 있다.
#   예) GROQ_MODEL=llama-3.3-70b-versatile + light openai/gpt-oss-20b
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
strip_all() {
  grep -vE '^(LLM_BASE_URL|LLM_MODEL|LLM_MODEL_LIGHT|LLM_API_KEY)=' "$ENV" > "$ENV.tmp" || true
  mv "$ENV.tmp" "$ENV"
}
strip_light() {
  grep -vE '^LLM_MODEL_LIGHT=' "$ENV" > "$ENV.tmp" || true
  mv "$ENV.tmp" "$ENV"
}

case "${1:-status}" in
  local)
    model="${2:-qwen2.5-coder:7b}"
    base="${OLLAMA_HOST:-http://localhost:11434}/v1"
    strip_all
    { echo "LLM_BASE_URL=$base"; echo "LLM_MODEL=$model"; echo "LLM_API_KEY=local"; } >> "$ENV"
    [ -n "${3:-}" ] && echo "LLM_MODEL_LIGHT=$3" >> "$ENV"
    echo "→ 로컬(Ollama) 전환: $model @ $base${3:+  (경량: $3)}"
    ;;
  groq)
    strip_all
    echo "→ Groq 복귀: LLM_* 별칭 제거 (GROQ_* 값 사용)"
    ;;
  light)
    [ -n "${2:-}" ] || { echo "usage: $0 light <모델>|off"; exit 2; }
    strip_light
    if [ "$2" = "off" ]; then
      echo "→ 경량 라우팅 해제 (모든 역할이 주 모델 사용)"
    else
      echo "LLM_MODEL_LIGHT=$2" >> "$ENV"
      echo "→ 경량 모델: $2 (요약·EDA 서술·도구선택·게이트 테스트 담당)"
    fi
    ;;
  status)
    echo "현재 .env LLM 설정:"
    grep -E '^(LLM_|GROQ_)(BASE_URL|MODEL|MODEL_LIGHT|API_KEY)=' "$ENV" | sed -E 's/(API_KEY=).*/\1***/' \
      || echo "  (LLM_* 없음 → Groq 기본값)"
    ;;
  *)
    echo "usage: $0 {local [model] [light]|groq|light <model>|off|status}"; exit 2 ;;
esac
