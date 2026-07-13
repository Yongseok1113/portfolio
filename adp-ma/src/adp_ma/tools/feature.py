"""Feature Engineering 도구 8종 (AutoKaggle Feature Engineering Tools 시드).

M1 시드 — 논문의 11종 중 핵심 8종. sklearn 없이 pandas/numpy로 구현해
샌드박스·워커 이미지 의존성을 늘리지 않는다 (모델링 도구는 M3에서 sklearn과 함께).
"""

import numpy as np
import pandas as pd

from adp_ma.tools.base import numeric_columns, register


@register(
    "one_hot_encode",
    "범주형 컬럼 원-핫 인코딩 (원본 컬럼은 제거)",
    {
        "columns": "대상 컬럼 목록",
        "max_categories": "카테고리 수 상한 — 초과 시 상위 빈도만 인코딩하고 나머지는 _other (기본 20)",
    },
)
def one_hot_encode(df, columns, max_categories=20):
    out = df.copy()
    max_categories = int(max_categories)
    for c in list(columns):
        if c not in out.columns:
            raise KeyError(f"one_hot_encode: 컬럼 '{c}' 없음")
        s = out[c].astype("string")
        top = s.value_counts().head(max_categories).index
        s = s.where(s.isin(top) | s.isna(), other="_other")
        dummies = pd.get_dummies(s, prefix=c, dtype="int64")
        out = pd.concat([out.drop(columns=[c]), dummies], axis=1)
    return out


@register(
    "frequency_encode",
    "범주형 값을 등장 빈도(비율)로 치환한 새 컬럼 <col>_freq 추가",
    {"columns": "대상 컬럼 목록"},
)
def frequency_encode(df, columns):
    out = df.copy()
    for c in list(columns):
        if c not in out.columns:
            raise KeyError(f"frequency_encode: 컬럼 '{c}' 없음")
        freq = out[c].value_counts(normalize=True)
        out[f"{c}_freq"] = out[c].map(freq)
    return out


@register(
    "label_encode",
    "범주형 값을 정수 코드로 치환한 새 컬럼 <col>_code 추가 (결측은 -1)",
    {"columns": "대상 컬럼 목록"},
)
def label_encode(df, columns):
    out = df.copy()
    for c in list(columns):
        if c not in out.columns:
            raise KeyError(f"label_encode: 컬럼 '{c}' 없음")
        out[f"{c}_code"] = pd.Categorical(out[c]).codes  # 결측 = -1
    return out


@register(
    "scale_features",
    "수치형 컬럼 스케일링 (제자리 변환)",
    {
        "columns": "대상 컬럼 (생략 시 수치형 전체)",
        "method": "standard(z-score, 기본)|minmax(0~1)",
    },
)
def scale_features(df, columns=None, method="standard"):
    out = df.copy()
    for c in numeric_columns(out, columns):
        s = out[c].astype("float64")
        if method == "standard":
            std = s.std()
            out[c] = (s - s.mean()) / std if std else s * 0.0
        elif method == "minmax":
            rng = s.max() - s.min()
            out[c] = (s - s.min()) / rng if rng else s * 0.0
        else:
            raise ValueError(f"scale_features: 알 수 없는 method '{method}'")
    return out


@register(
    "extract_datetime_features",
    "datetime 컬럼에서 파생 피처 추출 (<col>_year 등 새 컬럼 추가)",
    {
        "column": "datetime 컬럼 (문자열이면 자동 파싱)",
        "features": '["year","month","day","dayofweek","hour"] 중 선택 (기본 year/month/day/dayofweek)',
    },
)
def extract_datetime_features(df, column, features=None):
    out = df.copy()
    if column not in out.columns:
        raise KeyError(f"extract_datetime_features: 컬럼 '{column}' 없음")
    s = out[column]
    if not pd.api.types.is_datetime64_any_dtype(s):
        s = pd.to_datetime(s, errors="coerce", format="mixed")
    accessors = {
        "year": s.dt.year,
        "month": s.dt.month,
        "day": s.dt.day,
        "dayofweek": s.dt.dayofweek,
        "hour": s.dt.hour,
    }
    for f in features or ["year", "month", "day", "dayofweek"]:
        if f not in accessors:
            raise ValueError(f"extract_datetime_features: 알 수 없는 feature '{f}'")
        out[f"{column}_{f}"] = accessors[f]
    return out


@register(
    "bin_numeric",
    "수치형 컬럼을 구간화한 새 컬럼 <col>_bin 추가",
    {"column": "대상 컬럼", "bins": "구간 수 (기본 5)"},
)
def bin_numeric(df, column, bins=5):
    out = df.copy()
    if column not in out.columns:
        raise KeyError(f"bin_numeric: 컬럼 '{column}' 없음")
    out[f"{column}_bin"] = pd.cut(out[column], bins=int(bins), labels=False)
    return out


@register(
    "log_transform",
    "수치형 컬럼 로그 변환 log1p (음수는 NaN) — 새 컬럼 <col>_log 추가",
    {"columns": "대상 컬럼 목록"},
)
def log_transform(df, columns):
    out = df.copy()
    for c in list(columns):
        if c not in out.columns:
            raise KeyError(f"log_transform: 컬럼 '{c}' 없음")
        s = pd.to_numeric(out[c], errors="coerce")
        out[f"{c}_log"] = np.log1p(s.where(s >= 0))
    return out


@register(
    "remove_correlated",
    "상관계수가 threshold를 넘는 수치형 컬럼 쌍에서 뒤쪽 컬럼 제거",
    {"threshold": "절대 상관계수 임계값 (기본 0.95)", "exclude": "제거하면 안 되는 컬럼 목록"},
)
def remove_correlated(df, threshold=0.95, exclude=None):
    out = df.copy()
    exclude = set(exclude or [])
    nums = [c for c in numeric_columns(out) if c not in exclude]
    if len(nums) < 2:
        return out
    corr = out[nums].corr().abs()
    upper = corr.where(np.triu(np.ones(corr.shape, dtype=bool), k=1))
    drop = [c for c in upper.columns if (upper[c] > float(threshold)).any()]
    return out.drop(columns=drop)
