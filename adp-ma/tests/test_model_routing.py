"""역할별 모델 라우팅 — 티어 선택·사용량 집계 (네트워크 없이 목으로 검증)."""

import pandas as pd

from adp_ma.config import Settings
from adp_ma.llm import LLMClient


class _FakeUsage:
    def __init__(self, n):
        self.total_tokens = n


class _FakeResp:
    def __init__(self, text, tokens):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]
        self.usage = _FakeUsage(tokens)


def _client(monkeypatch, **over):
    for k in ("LLM_MODEL", "LLM_MODEL_LIGHT", "GROQ_MODEL", "GROQ_MODEL_LIGHT"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(_env_file=None, groq_api_key="x", **over)
    c = LLMClient(s)
    seen = []

    def fake_create(*, model, messages, temperature):
        seen.append(model)
        return _FakeResp("ok", 10)

    monkeypatch.setattr(c._client.chat.completions, "create", fake_create)
    return c, seen


def test_light_falls_back_to_main_when_unset(monkeypatch):
    c, seen = _client(monkeypatch, groq_model="big")
    c.chat("s", "u", light=True)
    c.chat("s", "u")
    assert seen == ["big", "big"]  # 경량 미설정 → 전부 주 모델 (기존 동작 보존)


def test_light_routes_to_light_model(monkeypatch):
    c, seen = _client(monkeypatch, groq_model="big", groq_model_light="small")
    c.chat("s", "u", light=True)
    c.chat("s", "u")
    assert seen == ["small", "big"]


def test_usage_tracked_per_model(monkeypatch):
    c, _ = _client(monkeypatch, groq_model="big", groq_model_light="small")
    c.chat("s", "u", light=True)
    c.chat("s", "u")
    c.chat("s", "u")
    assert c.calls == 3 and c.total_tokens == 30
    assert c.calls_by_model == {"small": 1, "big": 2}
    assert c.tokens_by_model == {"small": 10, "big": 20}


def test_chat_json_forwards_tier(monkeypatch):
    c, seen = _client(monkeypatch, groq_model="big", groq_model_light="small")

    def fake_create(*, model, messages, temperature):
        seen.append(model)
        return _FakeResp('{"a": 1}', 5)

    monkeypatch.setattr(c._client.chat.completions, "create", fake_create)
    assert c.chat_json("s", "u", light=True) == {"a": 1}
    assert seen == ["small"]


def test_llm_model_light_alias_overrides(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL_LIGHT", raising=False)
    monkeypatch.setenv("GROQ_MODEL_LIGHT", "from-groq")
    monkeypatch.setenv("LLM_MODEL_LIGHT", "from-llm")
    s = Settings(_env_file=None, groq_api_key="x")
    assert s.groq_model_light == "from-llm"  # LLM_* 우선


# ── 교차 엔드포인트 (경량 티어를 다른 서버로) ────────────────────────────────
def test_light_endpoint_uses_separate_client(monkeypatch):
    for k in ("LLM_MODEL", "LLM_MODEL_LIGHT", "LLM_BASE_URL_LIGHT"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(
        _env_file=None, groq_api_key="x",
        groq_model="big", groq_model_light="small",
        groq_base_url_light="http://localhost:11434/v1",
    )
    c = LLMClient(s)
    assert c._client_light is not c._client            # 별도 클라이언트
    assert c._client_for(light=True) is c._client_light
    assert c._client_for(light=False) is c._client
    assert c.endpoint_for(light=True) == "http://localhost:11434/v1"
    assert c.endpoint_for() == s.groq_base_url


def test_same_endpoint_reuses_client(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL_LIGHT", raising=False)
    s = Settings(_env_file=None, groq_api_key="x", groq_model="big", groq_model_light="small")
    c = LLMClient(s)
    assert c._client_light is c._client                # 경량 엔드포인트 미지정 → 재사용
    assert c.endpoint_for(light=True) == c.endpoint_for()


def test_light_endpoint_ignored_without_light_model(monkeypatch):
    """모델 없이 엔드포인트만 주면 라우팅이 성립하지 않으므로 설정 오류로 막는다."""
    import pytest

    for k in ("LLM_MODEL_LIGHT", "GROQ_MODEL_LIGHT"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="경량 모델"):
        Settings(
            _env_file=None, groq_api_key="x",
            groq_base_url_light="http://localhost:11434/v1",
        )


def test_light_endpoint_gets_dummy_key(monkeypatch):
    for k in ("LLM_API_KEY_LIGHT", "GROQ_API_KEY_LIGHT"):
        monkeypatch.delenv(k, raising=False)
    s = Settings(
        _env_file=None, groq_api_key="x", groq_model_light="small",
        groq_base_url_light="http://localhost:11434/v1",
    )
    assert s.groq_api_key_light == "local"  # 로컬 엔드포인트 + 키 없음 → 더미


def test_result_exposes_per_model_usage(monkeypatch, tmp_path):
    """라우팅 효과를 측정할 수 있도록 result에 모델별 사용량이 실린다."""
    from adp_ma.pipeline import PipelineRunner

    data = tmp_path / "d.csv"
    pd.DataFrame({"a": [1, 2]}).to_csv(data, index=False)
    s = Settings(
        _env_file=None, workflow="kaggle", groq_api_key="x",
        groq_model="big", groq_model_light="small", runs_dir=str(tmp_path / "runs"),
    )
    runner = PipelineRunner(s)
    runner.plan_reviewer = lambda phases: False  # LLM 호출 없이 즉시 중단
    runner.llm.calls_by_model = {"big": 2, "small": 3}
    runner.llm.tokens_by_model = {"big": 100, "small": 50}

    res = runner.run(data, "goal")
    assert res.calls_by_model == {"big": 2, "small": 3}
    assert res.tokens_by_model == {"big": 100, "small": 50}
