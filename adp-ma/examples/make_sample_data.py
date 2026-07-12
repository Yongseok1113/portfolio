"""데모용 지저분한 판매 데이터 생성 — examples/sales_raw.csv

의도적으로 넣은 오염:
- amount: "$1,234.50" 형태 문자열 + 결측
- order_date: 두 가지 날짜 포맷 혼재
- region: 대소문자 불일치 + 결측
- 중복 주문 행
"""

import random
from pathlib import Path

import pandas as pd

random.seed(42)

REGIONS = ["Seoul", "seoul", "BUSAN", "Busan", "Incheon", None]
N = 500

rows = []
for i in range(N):
    date = (
        f"2026-{random.randint(1, 6):02d}-{random.randint(1, 28):02d}"
        if random.random() < 0.5
        else f"{random.randint(1, 28):02d}/{random.randint(1, 6):02d}/2026"
    )
    amount = None if random.random() < 0.08 else f"${random.uniform(10, 5000):,.2f}"
    rows.append(
        {
            "order_id": f"ORD-{i:05d}",
            "order_date": date,
            "region": random.choice(REGIONS),
            "amount": amount,
            "quantity": random.randint(1, 20),
        }
    )

df = pd.DataFrame(rows)
df = pd.concat([df, df.sample(25, random_state=1)])  # 중복 주입

out = Path(__file__).parent / "sales_raw.csv"
df.to_csv(out, index=False)
print(f"생성: {out} ({len(df)} rows)")
print("예시 goal: 중복 제거, amount를 숫자로 정규화, region 표준화, 날짜 파싱 후 월별·지역별 매출 집계")
