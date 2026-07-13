"""ML tools library 결정적 테스트 — LLM·클러스터 불필요."""

import numpy as np
import pandas as pd
import pytest

from adp_ma.tools import TOOLS, describe_tools, execute_tool_plan, validate_tool_plan


def sample_df():
    return pd.DataFrame(
        {
            "amount": [10.0, None, 30.0, 1000.0, 20.0],
            "region": ["Seoul", "seoul", None, "Busan", "Seoul"],
            "order_date": ["2026-01-05", "2026-02-10", "2026-01-05", None, "2026-03-01"],
            "order_id": ["a", "b", "a", "c", "d"],
        }
    )


# ── 레지스트리 ────────────────────────────────────────────────────────────────
def test_registry_seeded_with_cleaning_and_feature_tools():
    expected = {
        # cleaning 7종
        "fill_missing", "remove_sparse_columns", "handle_outliers_zscore",
        "handle_outliers_iqr", "remove_duplicates", "convert_dtypes", "format_datetime",
        "parse_numeric",
        # feature 시드
        "one_hot_encode", "frequency_encode", "label_encode", "scale_features",
        "extract_datetime_features", "bin_numeric", "log_transform", "remove_correlated",
    }
    assert expected <= set(TOOLS)
    catalog = describe_tools()
    for name in expected:
        assert name in catalog  # LLM 카탈로그에 전부 노출


# ── cleaning ─────────────────────────────────────────────────────────────────
def test_fill_missing_auto():
    out = TOOLS["fill_missing"].run(sample_df())
    assert out["amount"].isna().sum() == 0
    assert out["region"].isna().sum() == 0
    assert out.loc[1, "amount"] == 25.0  # median of [10,30,1000,20]


def test_remove_duplicates_subset():
    out = TOOLS["remove_duplicates"].run(sample_df(), subset=["order_id"])
    assert list(out["order_id"]) == ["a", "b", "c", "d"]


def test_outliers_clip_keeps_rows():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0, 2.0, 100.0]})
    out = TOOLS["handle_outliers_iqr"].run(df, method="clip")
    assert len(out) == 5  # clip은 행을 보존
    assert out["x"].max() < 100.0


def test_convert_dtypes_coerces_without_dropping():
    df = pd.DataFrame({"v": ["1", "2", "oops"]})
    out = TOOLS["convert_dtypes"].run(df, mapping={"v": "float"})
    assert len(out) == 3
    assert out["v"].isna().sum() == 1  # 파싱 불가는 NaN 보존


def test_parse_numeric_formatted_strings():
    df = pd.DataFrame({"amount": ["$1,234.56", "45%", "1 200", None, "N/A"]})
    out = TOOLS["parse_numeric"].run(df, columns=["amount"])
    assert out.loc[0, "amount"] == 1234.56
    assert out.loc[1, "amount"] == 45.0
    assert out.loc[2, "amount"] == 1200.0
    assert pd.isna(out.loc[3, "amount"])  # 원래 결측 유지
    assert pd.isna(out.loc[4, "amount"])  # 파싱 불가는 NaN (행 보존)


def test_format_datetime_mixed():
    out = TOOLS["format_datetime"].run(sample_df(), columns=["order_date"])
    assert pd.api.types.is_datetime64_any_dtype(out["order_date"])
    assert out["order_date"].isna().sum() == 1  # 원래 None만 NaT


# ── feature ──────────────────────────────────────────────────────────────────
def test_one_hot_encode_max_categories():
    df = pd.DataFrame({"c": ["a", "b", "c", "d", "a"]})
    out = TOOLS["one_hot_encode"].run(df, columns=["c"], max_categories=2)
    assert "c" not in out.columns
    assert "c__other" in out.columns  # 상위 2개 외는 _other로 묶임


def test_frequency_and_label_encode_add_columns():
    out = TOOLS["frequency_encode"].run(sample_df(), columns=["region"])
    out = TOOLS["label_encode"].run(out, columns=["region"])
    assert {"region_freq", "region_code"} <= set(out.columns)
    assert out.loc[2, "region_code"] == -1  # 결측 = -1


def test_scale_standard():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    out = TOOLS["scale_features"].run(df, columns=["x"])
    assert abs(out["x"].mean()) < 1e-9


def test_extract_datetime_features():
    df = TOOLS["format_datetime"].run(sample_df(), columns=["order_date"])
    out = TOOLS["extract_datetime_features"].run(df, column="order_date", features=["month"])
    assert list(out["order_date_month"].dropna().unique()) == [1, 2, 3]


def test_remove_correlated_drops_duplicate_signal():
    df = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [2.0, 4.0, 6.0, 8.0], "c": [4.0, 1.0, 3.0, 2.0]})
    out = TOOLS["remove_correlated"].run(df, threshold=0.99)
    assert "b" not in out.columns and {"a", "c"} <= set(out.columns)


# ── tool plan 실행기 ─────────────────────────────────────────────────────────
def test_validate_rejects_unknown_tool_and_params():
    assert "알 수 없는 도구" in validate_tool_plan([{"tool": "nope"}])
    assert "알 수 없는 파라미터" in validate_tool_plan(
        [{"tool": "remove_duplicates", "params": {"bogus": 1}}]
    )
    assert "필수 파라미터 누락" in validate_tool_plan([{"tool": "one_hot_encode", "params": {}}])
    assert validate_tool_plan([{"tool": "remove_duplicates", "params": {}}]) == ""


def test_execute_tool_plan_chain():
    plan = [
        {"tool": "remove_duplicates", "params": {"subset": ["order_id"]}},
        {"tool": "fill_missing", "params": {"columns": ["amount"], "method": "median"}},
        {"tool": "format_datetime", "params": {"columns": ["order_date"]}},
    ]
    res = execute_tool_plan(plan, sample_df())
    assert res.ok and res.steps_done == 3
    assert len(res.df) == 4
    assert res.df["amount"].isna().sum() == 0


def test_execute_tool_plan_runtime_error_reports_step():
    plan = [{"tool": "one_hot_encode", "params": {"columns": ["ghost"]}}]
    res = execute_tool_plan(plan, sample_df())
    assert not res.ok
    assert "step 0" in res.error and "ghost" in res.error
