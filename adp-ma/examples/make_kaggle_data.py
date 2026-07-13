"""Kaggle 형식 합성 분류 데이터 생성 — examples/kaggle/{train,test,sample_submission,truth}.csv

과제: 고객 이탈(churn) 예측. 클리닝·FE가 실제로 필요하도록 오염 주입:
- monthly_fee: "$29.99" 형태 통화 문자열 + 결측
- signup_date: 두 가지 날짜 형식 혼재
- plan: 대소문자 불일치 (Basic/basic/PRO ...)
- 중복 행 (train에만)

라벨은 피처와 실제 상관을 갖게 생성 → 모델이 우연 이상을 학습할 수 있음.
truth.csv(test 정답)는 벤치마크 채점용 — 파이프라인에는 주지 않는다.
"""

import random
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)
random.seed(42)

N = 900
PLANS = ["Basic", "basic", "PRO", "Pro", "premium", "Premium"]

fee = rng.uniform(10, 120, N)
usage = rng.uniform(0, 300, N).round(1)
tenure = rng.integers(1, 60, N)
support_calls = rng.poisson(2, N)

# 이탈 확률: 요금 높고, 사용량 적고, 가입기간 짧고, 문의 많을수록 ↑
logit = 0.03 * fee - 0.012 * usage - 0.05 * tenure + 0.35 * support_calls - 1.2
churn = (1 / (1 + np.exp(-logit)) > rng.uniform(0, 1, N)).astype(int)

rows = []
for i in range(N):
    date = (
        f"2025-{rng.integers(1, 13):02d}-{rng.integers(1, 29):02d}"
        if rng.uniform() < 0.5
        else f"{rng.integers(1, 29):02d}/{rng.integers(1, 13):02d}/2025"
    )
    rows.append(
        {
            "customer_id": f"CUST-{i:05d}",
            "plan": random.choice(PLANS),
            "signup_date": date,
            "monthly_fee": None if rng.uniform() < 0.07 else f"${fee[i]:,.2f}",
            "usage_hours": usage[i],
            "tenure_months": int(tenure[i]),
            "support_calls": int(support_calls[i]),
            "churn": int(churn[i]),
        }
    )

df = pd.DataFrame(rows)
train, test = df.iloc[:700].copy(), df.iloc[700:].copy()
train = pd.concat([train, train.sample(30, random_state=1)])  # 중복 주입 (train만)

out = Path(__file__).parent / "kaggle"
out.mkdir(exist_ok=True)
train.to_csv(out / "train.csv", index=False)
test.drop(columns=["churn"]).to_csv(out / "test.csv", index=False)
test[["customer_id", "churn"]].to_csv(out / "truth.csv", index=False)
pd.DataFrame({"customer_id": test["customer_id"], "churn": 0}).to_csv(
    out / "sample_submission.csv", index=False
)
print(f"생성: {out}/ train={len(train)} test={len(test)} (양성비율 {churn.mean():.2f})")
