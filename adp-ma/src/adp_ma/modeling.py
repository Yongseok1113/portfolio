"""모델링 (AutoKaggle §Modeling Tools — M3).

train_validate_predict: 후보 모델을 교차검증으로 비교·선택하고
전체 학습 후 test를 예측한다. LLM 생성 코드가 아닌 검증된 자체 코드이므로
샌드박스 밖(controller)에서 실행한다 — sklearn을 샌드박스 화이트리스트에
추가하지 않는다.
"""

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class ModelReport:
    task: str                      # "classification" | "regression"
    target: str
    features: list[str]
    best_model: str
    best_score: float
    metric: str                    # "accuracy" | "neg_rmse"
    cv_scores: dict[str, float] = field(default_factory=dict)
    n_train: int = 0
    n_test: int = 0


def infer_task(y: pd.Series) -> str:
    """target 특성으로 분류/회귀 판별 — 저카디널리티(≤20) 정수/문자면 분류."""
    if not pd.api.types.is_numeric_dtype(y):
        return "classification"
    nunique = y.nunique(dropna=True)
    if nunique <= 2:  # 이진이면 dtype이 float로 오염됐어도 분류
        return "classification"
    if nunique <= 20 and pd.api.types.is_integer_dtype(y.dropna()):
        return "classification"
    return "regression"


def _feature_matrix(train: pd.DataFrame, test: pd.DataFrame, target: str, exclude: list[str]):
    """수치형 공통 피처만 사용, 결측은 train 중앙값으로 대치 (leakage 방지)."""
    drop = set(exclude) | {target}
    cols = [
        c for c in train.columns
        if c not in drop
        and c in test.columns
        and pd.api.types.is_numeric_dtype(train[c])
        and pd.api.types.is_numeric_dtype(test[c])
    ]
    if not cols:
        raise ValueError("모델링에 사용할 수치형 공통 피처가 없음 — FE 단계 산출 확인 필요")
    med = train[cols].median(numeric_only=True)
    # med[col]이 NaN인 경우(해당 컬럼이 train에서 전부 결측) fillna(med)는 NaN을 남긴다.
    # 결정적 모델링 단계는 sklearn에 NaN을 절대 넘기면 안 되므로 0으로 최종 방어한다.
    x_train = train[cols].fillna(med).fillna(0.0).astype("float64")
    x_test = test[cols].fillna(med).fillna(0.0).astype("float64")
    return x_train, x_test, cols


def _candidates(task: str):
    from sklearn.ensemble import (
        GradientBoostingClassifier,
        GradientBoostingRegressor,
        RandomForestClassifier,
        RandomForestRegressor,
    )
    from sklearn.linear_model import LogisticRegression, Ridge

    if task == "classification":
        return {
            "logistic_regression": LogisticRegression(max_iter=1000),
            "random_forest": RandomForestClassifier(n_estimators=200, random_state=0),
            "gradient_boosting": GradientBoostingClassifier(random_state=0),
        }
    return {
        "ridge": Ridge(),
        "random_forest": RandomForestRegressor(n_estimators=200, random_state=0),
        "gradient_boosting": GradientBoostingRegressor(random_state=0),
    }


def train_validate_predict(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str,
    *,
    task: str | None = None,
    exclude: list[str] | None = None,
    cv: int = 5,
) -> tuple[ModelReport, np.ndarray]:
    """후보 모델 CV 비교 → 최고 모델 전체 학습 → test 예측."""
    from sklearn.model_selection import cross_val_score

    if target not in train.columns:
        raise KeyError(f"target 컬럼 '{target}' 이 train에 없음")
    labeled = train.dropna(subset=[target])
    y = labeled[target]
    resolved_task = task or infer_task(y)
    x_train, x_test, features = _feature_matrix(labeled, test, target, exclude or [])

    if resolved_task == "classification":
        scoring, metric = "accuracy", "accuracy"
    else:
        scoring, metric = "neg_root_mean_squared_error", "neg_rmse"

    cv_scores: dict[str, float] = {}
    for name, model in _candidates(resolved_task).items():
        scores = cross_val_score(model, x_train, y, cv=cv, scoring=scoring)
        cv_scores[name] = round(float(scores.mean()), 4)

    best_model_name = max(cv_scores, key=cv_scores.get)
    best = _candidates(resolved_task)[best_model_name]
    best.fit(x_train, y)
    preds = best.predict(x_test)

    report = ModelReport(
        task=resolved_task,
        target=target,
        features=features,
        best_model=best_model_name,
        best_score=cv_scores[best_model_name],
        metric=metric,
        cv_scores=cv_scores,
        n_train=len(x_train),
        n_test=len(x_test),
    )
    return report, preds


def build_submission(
    sample_submission: pd.DataFrame,
    test: pd.DataFrame,
    predictions: np.ndarray,
) -> pd.DataFrame:
    """sample_submission 스키마(첫 컬럼=id, 둘째=target)에 맞춰 제출본 생성."""
    if sample_submission.shape[1] < 2:
        raise ValueError("sample_submission은 최소 2개 컬럼(id, target)이 필요")
    id_col, target_col = sample_submission.columns[0], sample_submission.columns[1]
    if id_col not in test.columns:
        raise KeyError(f"id 컬럼 '{id_col}' 이 test에 없음 — 파이프라인에서 보존돼야 함")
    sub = pd.DataFrame({id_col: test[id_col].values, target_col: predictions})
    # sample과 dtype 맞추기 (예: int 라벨)
    try:
        sub[target_col] = sub[target_col].astype(sample_submission[target_col].dtype)
    except (ValueError, TypeError):
        pass
    return sub
