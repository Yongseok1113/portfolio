import pandas as pd

from adp_ma.contracts import SchemaContract, verify_contract
from adp_ma.ground import GroundAgentSpec, run_agent_code, run_ladder

GOOD_CODE = "def run(df):\n    df['b'] = df['a'] * 2\n    return df\n"
BAD_CODE = "def run(df):\n    return df.no_such_method()\n"


def spec_with(code: str) -> GroundAgentSpec:
    return GroundAgentSpec(name="t", objective="a*2를 b로 추가", code=code)


def test_ladder_promotes_and_succeeds():
    df = pd.DataFrame({"a": range(2000)})
    result = run_ladder(spec_with(GOOD_CODE), df, refine=lambda s, e: s.code)
    assert result.ok
    assert result.level_reached == "FULL"
    assert result.revisions == 0
    assert len(result.df) == 2000
    assert (result.df["b"] == result.df["a"] * 2).all()


def test_refinement_fixes_failing_code():
    errors_seen = []

    def refine(spec, error):
        errors_seen.append(error)
        return GOOD_CODE

    result = run_ladder(spec_with(BAD_CODE), pd.DataFrame({"a": range(50)}), refine)
    assert result.ok
    assert result.revisions == 1
    assert "no_such_method" in errors_seen[0]


def test_gives_up_after_max_refines():
    result = run_ladder(
        spec_with(BAD_CODE),
        pd.DataFrame({"a": range(50)}),
        refine=lambda s, e: BAD_CODE,  # 계속 실패하는 코드
        max_refine_per_level=3,
    )
    assert not result.ok
    assert result.revisions == 3
    assert result.level_reached == "XS"


def test_contract_violation_triggers_refine():
    # 코드는 돌지만 계약(b 컬럼 추가)을 어기는 경우 → refine 호출
    noop = "def run(df):\n    return df\n"
    contract = SchemaContract(add_columns={"b": "int"})
    spec = GroundAgentSpec(name="t", objective="o", contract=contract, code=noop)
    calls = []

    def refine(s, error):
        calls.append(error)
        return GOOD_CODE

    result = run_ladder(
        spec,
        pd.DataFrame({"a": range(20)}),
        refine,
        verify=lambda din, dout: verify_contract(contract, din, dout),
    )
    assert result.ok
    assert calls and "SchemaContract" in calls[0]


def test_sandbox_blocks_file_and_import():
    df = pd.DataFrame({"a": [1]})
    res = run_agent_code("def run(df):\n    open('/etc/passwd')\n    return df\n", df)
    assert not res.ok and "차단" in res.error

    res = run_agent_code("import os\ndef run(df):\n    return df\n", df)
    assert not res.ok and "허용되지 않은 import" in res.error


def test_sandbox_allows_whitelisted_import():
    code = "import re\ndef run(df):\n    df['ok'] = df['a'].astype(str).str.match(r'\\d+')\n    return df\n"
    res = run_agent_code(code, pd.DataFrame({"a": [1, 2]}))
    assert res.ok
    assert res.df["ok"].all()


def test_sandbox_requires_dataframe_return():
    res = run_agent_code("def run(df):\n    return 42\n", pd.DataFrame({"a": [1]}))
    assert not res.ok and "DataFrame" in res.error
