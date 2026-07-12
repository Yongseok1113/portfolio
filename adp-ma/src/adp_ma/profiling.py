"""Stage 1 — Data Understanding: 입력 데이터의 스키마·통계 프로파일."""

import json

import pandas as pd


def profile_dataframe(df: pd.DataFrame, max_sample_values: int = 5) -> dict:
    columns: dict[str, dict] = {}
    for name in df.columns:
        s = df[name]
        info: dict = {
            "dtype": str(s.dtype),
            "null_rate": round(float(s.isna().mean()), 4),
            "n_unique": int(s.nunique(dropna=True)),
        }
        if pd.api.types.is_numeric_dtype(s):
            desc = s.describe()
            info["stats"] = {
                k: round(float(desc[k]), 4) for k in ("min", "max", "mean") if k in desc
            }
        else:
            info["sample_values"] = [
                str(v) for v in s.dropna().unique()[:max_sample_values]
            ]
        columns[str(name)] = info
    return {
        "n_rows": int(len(df)),
        "n_cols": int(df.shape[1]),
        "overall_null_rate": overall_null_rate(df),
        "columns": columns,
    }


def overall_null_rate(df: pd.DataFrame) -> float:
    if df.size == 0:
        return 0.0
    return round(float(df.isna().to_numpy().mean()), 4)


def profile_to_prompt(profile: dict) -> str:
    return json.dumps(profile, ensure_ascii=False, indent=1)
