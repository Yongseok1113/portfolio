"""데이터 클리닝 도구 7종 (AutoKaggle Data Cleaning Tools 대응)."""

import pandas as pd

from adp_ma.tools.base import numeric_columns, register


@register(
    "fill_missing",
    "결측값 대치. 수치형은 mean/median, 범주형은 mode 또는 상수",
    {
        "columns": "대상 컬럼 목록 (생략 시 결측 있는 전체)",
        "method": "auto|mean|median|mode|constant (기본 auto: 수치=median, 그 외=mode)",
        "value": "method=constant일 때 채울 값",
    },
)
def fill_missing(df, columns=None, method="auto", value=None):
    out = df.copy()
    targets = [c for c in (columns or out.columns) if c in out.columns and out[c].isna().any()]
    for c in targets:
        s = out[c]
        m = method
        if m == "auto":
            m = "median" if pd.api.types.is_numeric_dtype(s) else "mode"
        if m == "mean":
            out[c] = s.fillna(s.mean())
        elif m == "median":
            out[c] = s.fillna(s.median())
        elif m == "mode":
            mode = s.mode(dropna=True)
            out[c] = s.fillna(mode.iloc[0] if not mode.empty else s)
        elif m == "constant":
            out[c] = s.fillna(value)
        else:
            raise ValueError(f"fill_missing: 알 수 없는 method '{method}'")
    return out


@register(
    "remove_sparse_columns",
    "결측 비율이 threshold를 넘는 컬럼 제거",
    {"threshold": "결측 비율 임계값 0~1 (기본 0.5)"},
)
def remove_sparse_columns(df, threshold=0.5):
    keep = [c for c in df.columns if df[c].isna().mean() <= float(threshold)]
    return df[keep].copy()


@register(
    "handle_outliers_zscore",
    "z-score 기준 이상치 처리 (수치형)",
    {
        "columns": "대상 컬럼 (생략 시 수치형 전체)",
        "threshold": "z 임계값 (기본 3.0)",
        "method": "clip(경계로 자름, 기본)|remove(행 제거)",
    },
)
def handle_outliers_zscore(df, columns=None, threshold=3.0, method="clip"):
    out = df.copy()
    threshold = float(threshold)
    for c in numeric_columns(out, columns):
        s = out[c]
        mean, std = s.mean(), s.std()
        if not std or pd.isna(std):
            continue
        lo, hi = mean - threshold * std, mean + threshold * std
        if method == "clip":
            out[c] = s.clip(lo, hi)
        elif method == "remove":
            out = out[(s.isna()) | ((s >= lo) & (s <= hi))]
        else:
            raise ValueError(f"handle_outliers_zscore: 알 수 없는 method '{method}'")
    return out


@register(
    "handle_outliers_iqr",
    "IQR 기준 이상치 처리 (수치형)",
    {
        "columns": "대상 컬럼 (생략 시 수치형 전체)",
        "factor": "IQR 배수 (기본 1.5)",
        "method": "clip(기본)|remove",
    },
)
def handle_outliers_iqr(df, columns=None, factor=1.5, method="clip"):
    out = df.copy()
    factor = float(factor)
    for c in numeric_columns(out, columns):
        s = out[c]
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if not iqr or pd.isna(iqr):
            continue
        lo, hi = q1 - factor * iqr, q3 + factor * iqr
        if method == "clip":
            out[c] = s.clip(lo, hi)
        elif method == "remove":
            out = out[(s.isna()) | ((s >= lo) & (s <= hi))]
        else:
            raise ValueError(f"handle_outliers_iqr: 알 수 없는 method '{method}'")
    return out


@register(
    "remove_duplicates",
    "중복 행 제거",
    {"subset": "중복 판정 컬럼 목록 (생략 시 전체 컬럼)", "keep": "first(기본)|last"},
)
def remove_duplicates(df, subset=None, keep="first"):
    return df.drop_duplicates(subset=subset, keep=keep).copy()


@register(
    "parse_numeric",
    "서식 있는 숫자 문자열('$1,234.56', '45%', '1 200')을 숫자로 파싱 — 통화기호·쉼표·공백 제거 후 변환, 실패는 NaN 보존",
    {"columns": "대상 컬럼 목록"},
)
def parse_numeric(df, columns):
    out = df.copy()
    for c in list(columns):
        if c not in out.columns:
            raise KeyError(f"parse_numeric: 컬럼 '{c}' 없음")
        s = out[c]
        was_na = s.isna()
        cleaned = s.astype("string").str.replace(r"[^\d.eE+-]", "", regex=True)
        num = pd.to_numeric(cleaned, errors="coerce")
        out[c] = num.mask(was_na)  # 원래 결측은 결측으로 유지
    return out


@register(
    "convert_dtypes",
    "컬럼 dtype 단순 변환 (파싱 불가 값은 NaN 보존). 주의: '$1,234' 같은 서식 문자열은 parse_numeric을 쓸 것",
    {"mapping": '{"컬럼": "int|float|str|category|bool|datetime"} 형태'},
)
def convert_dtypes(df, mapping):
    out = df.copy()
    for col, dtype in dict(mapping).items():
        if col not in out.columns:
            raise KeyError(f"convert_dtypes: 컬럼 '{col}' 없음")
        s = out[col]
        if dtype in ("int", "float"):
            num = pd.to_numeric(s, errors="coerce")
            out[col] = num.astype("Int64") if dtype == "int" else num
        elif dtype == "datetime":
            out[col] = pd.to_datetime(s, errors="coerce", format="mixed")
        elif dtype in ("str", "string"):
            out[col] = s.astype("string")
        elif dtype == "category":
            out[col] = s.astype("category")
        elif dtype == "bool":
            out[col] = s.astype("boolean")
        else:
            raise ValueError(f"convert_dtypes: 알 수 없는 dtype '{dtype}'")
    return out


@register(
    "format_datetime",
    "문자열 컬럼을 datetime으로 파싱 (형식 혼재 허용, 실패는 NaT)",
    {"columns": "대상 컬럼 목록", "dayfirst": "DD/MM/YYYY 형식이면 true (기본 false)"},
)
def format_datetime(df, columns, dayfirst=False):
    out = df.copy()
    for c in list(columns):
        if c not in out.columns:
            raise KeyError(f"format_datetime: 컬럼 '{c}' 없음")
        out[c] = pd.to_datetime(out[c], errors="coerce", format="mixed", dayfirst=bool(dayfirst))
    return out
