"""config LLM_* 별칭 우선순위 테스트."""

from adp_ma.config import Settings


def test_llm_aliases_override_groq(monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "GROQ_BASE_URL", "GROQ_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "qwen2.5-coder:7b")
    s = Settings(_env_file=None)
    assert s.groq_base_url == "http://localhost:11434/v1"  # LLM_* 가 이김
    assert s.groq_model == "qwen2.5-coder:7b"
    assert s.groq_api_key == "local"  # 로컬 엔드포인트 + 키 없음 → 더미 채움


def test_groq_used_when_no_llm_alias(monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_real")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    s = Settings(_env_file=None)
    assert s.groq_base_url == "https://api.groq.com/openai/v1"
    assert s.groq_model == "llama-3.3-70b-versatile"
    assert s.groq_api_key == "gsk_real"  # 실제 키 보존, 더미로 안 덮음


def test_local_endpoint_keeps_explicit_key(monkeypatch):
    for k in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("LLM_API_KEY", "explicit-token")
    s = Settings(_env_file=None)
    assert s.groq_api_key == "explicit-token"  # 명시 키는 더미로 안 덮음
