"""M4 — 단위 테스트 게이트·HITL 결정적 테스트 (LLM 불필요)."""

from pathlib import Path

import pandas as pd

from adp_ma.ground import GroundAgentSpec, run_ladder
from adp_ma.ground.sandbox import run_gate_code

GOOD_CODE = "def run(df):\n    df['b'] = df['a'] * 2\n    return df\n"


# ── run_gate_code (샌드박스) ─────────────────────────────────────────────────
def test_gate_code_pass():
    code = "def check(df_in, df_out):\n    assert 'b' in df_out.columns, 'b 없음'\n"
    df = pd.DataFrame({"a": [1]})
    out = pd.DataFrame({"a": [1], "b": [2]})
    assert run_gate_code(code, df, out) == ""


def test_gate_code_assertion_failure_message():
    code = "def check(df_in, df_out):\n    assert len(df_out) == len(df_in), '행 수 변경'\n"
    err = run_gate_code(code, pd.DataFrame({"a": [1, 2]}), pd.DataFrame({"a": [1]}))
    assert "행 수 변경" in err


def test_gate_code_requires_check_function():
    assert "check" in run_gate_code("x = 1\n", pd.DataFrame(), pd.DataFrame())


def test_gate_code_sandboxed():
    err = run_gate_code("import os\ndef check(a, b):\n    pass\n", pd.DataFrame(), pd.DataFrame())
    assert "허용되지 않은 import" in err


# ── ladder 통합: 게이트는 M/FULL에서만, 실패 시 refine ───────────────────────
def test_gate_runs_only_at_m_and_full():
    calls = []

    def gate(d_in, d_out):
        calls.append(len(d_in))
        return ""

    spec = GroundAgentSpec(name="t", objective="o", code=GOOD_CODE)
    df = pd.DataFrame({"a": range(2000)})
    result = run_ladder(spec, df, refine=lambda s, e: s.code, gate=gate)
    assert result.ok
    assert calls == [1000, 2000]  # XS(10)·S(100)에서는 호출 안 됨


def test_gate_failure_triggers_refine():
    refines = []

    def gate(d_in, d_out):
        return "" if "fixed" in spec.code else "로직 오류: b가 a*2가 아님"

    def refine(s, error):
        refines.append(error)
        return GOOD_CODE + "# fixed\n"

    spec = GroundAgentSpec(name="t", objective="o", code=GOOD_CODE)
    df = pd.DataFrame({"a": range(2000)})
    result = run_ladder(spec, df, refine=refine, gate=gate)
    assert result.ok
    assert refines and "단위 테스트 실패" in refines[0]


# ── 예외 안전: 예상 밖 오류에도 결과·감사 추적이 남는다 ──────────────────────
def test_unexpected_error_still_produces_result(tmp_path):
    from adp_ma.config import Settings
    from adp_ma.pipeline import PipelineRunner

    data = tmp_path / "d.csv"
    pd.DataFrame({"a": [1, 2]}).to_csv(data, index=False)

    settings = Settings(workflow="kaggle", groq_api_key="dummy", runs_dir=str(tmp_path / "runs"))
    runner = PipelineRunner(settings)

    def boom(*a, **k):
        raise RuntimeError("LLM 429 같은 예상 밖 오류")

    runner.reader.brief = boom  # 첫 LLM 접점에서 폭발시킴

    result = runner.run(data, "goal")
    assert not result.ok
    assert "실행 오류" in result.message and "RuntimeError" in result.message
    # 크래시 대신 result.json까지 저장됨 (아카이브 경로도 이 지점을 지남)
    assert (tmp_path / "runs" / Path(result.run_dir).name / "result.json").exists()


# ── HITL: 계획 반려 시 LLM 실행 없이 중단 ────────────────────────────────────
def test_plan_reviewer_rejection_stops_run(tmp_path):
    from adp_ma.config import Settings
    from adp_ma.pipeline import PipelineRunner

    data = tmp_path / "d.csv"
    pd.DataFrame({"a": [1, 2, 3]}).to_csv(data, index=False)

    settings = Settings(workflow="kaggle", groq_api_key="dummy", runs_dir=str(tmp_path / "runs"))
    runner = PipelineRunner(settings)
    seen = []
    runner.plan_reviewer = lambda phases: (seen.extend(p.name for p in phases), False)[-1]

    result = runner.run(data, "goal")
    assert not result.ok
    assert "반려" in result.message
    assert "data_cleaning" in seen           # 계획은 리뷰어에게 전달됐고
    assert result.llm_calls == 0             # LLM 호출 전에 중단됨