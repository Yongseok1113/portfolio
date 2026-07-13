"""모델링 모듈 결정적 테스트 — LLM 불필요 (sklearn random_state 고정)."""

import numpy as np
import pandas as pd
import pytest

from adp_ma.modeling import (
    build_submission,
    infer_task,
    train_validate_predict,
)


def make_classification_frames(n=300):
    rng = np.random.default_rng(0)
    x1, x2 = rng.normal(0, 1, n), rng.normal(0, 1, n)
    y = ((x1 + x2 + rng.normal(0, 0.3, n)) > 0).astype(int)
    df = pd.DataFrame({"id": range(n), "x1": x1, "x2": x2, "noise": rng.normal(0, 1, n), "y": y})
    return df.iloc[:200].copy(), df.iloc[200:].drop(columns=["y"]).copy(), df.iloc[200:]["y"]


def test_infer_task():
    assert infer_task(pd.Series([0, 1, 1, 0])) == "classification"
    assert infer_task(pd.Series(["a", "b"])) == "classification"
    assert infer_task(pd.Series([1.5, 2.7, 3.14, 0.2])) == "regression"
    # 파이프라인이 target을 float로 오염시켜도 이진이면 분류로 판별
    assert infer_task(pd.Series([0.0, 1.0, 1.0, None])) == "classification"


def test_train_validate_predict_classification():
    train, test, y_true = make_classification_frames()
    report, preds = train_validate_predict(train, test, "y", exclude=["id"], cv=3)

    assert report.task == "classification"
    assert report.best_model in report.cv_scores
    assert set(report.features) == {"x1", "x2", "noise"}  # id·y 제외
    assert len(preds) == len(test)
    # 신호가 뚜렷한 데이터 — 학습이 됐다면 정확도가 우연(0.5)보다 훨씬 높아야 함
    assert (preds == y_true.values).mean() > 0.8


def test_train_handles_nan_and_excludes_target_nulls():
    train, test, _ = make_classification_frames()
    train.loc[train.index[:20], "x1"] = None   # 피처 결측 → 중앙값 대치
    train.loc[train.index[:10], "y"] = None    # 라벨 결측 → 학습 제외
    report, preds = train_validate_predict(train, test, "y", exclude=["id"], cv=3)
    assert report.n_train == 190
    assert len(preds) == len(test)


def test_no_numeric_features_raises():
    train = pd.DataFrame({"id": [1, 2], "name": ["a", "b"], "y": [0, 1]})
    test = pd.DataFrame({"id": [3], "name": ["c"]})
    with pytest.raises(ValueError, match="수치형 공통 피처"):
        train_validate_predict(train, test, "y", exclude=["id"], cv=2)


def test_build_submission_matches_sample_schema():
    sample = pd.DataFrame({"customer_id": ["a", "b"], "churn": [0, 0]})
    test = pd.DataFrame({"customer_id": ["c", "d"], "x": [1, 2]})
    sub = build_submission(sample, test, np.array([1.0, 0.0]))
    assert list(sub.columns) == ["customer_id", "churn"]
    assert list(sub["customer_id"]) == ["c", "d"]
    assert sub["churn"].dtype == sample["churn"].dtype  # int로 캐스팅됨


def test_build_submission_requires_id_in_test():
    sample = pd.DataFrame({"customer_id": ["a"], "churn": [0]})
    test = pd.DataFrame({"other": [1]})
    with pytest.raises(KeyError, match="customer_id"):
        build_submission(sample, test, np.array([1]))
