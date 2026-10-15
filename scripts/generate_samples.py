"""Generate the deterministic demo datasets in samples/. Run: python scripts/generate_samples.py"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples"


def ecommerce_orders(rows: int = 2400) -> None:
    rng = random.Random(42)
    regions = [("North", 1.15), ("South", 0.9), ("East", 1.0), ("West", 1.05)]
    channels = [("web", 0.55), ("mobile", 0.35), ("marketplace", 0.10)]
    start = date(2026, 1, 5)
    out = []
    for i in range(1, rows + 1):
        region, region_lift = rng.choice(regions)
        channel = rng.choices([c for c, _ in channels], [w for _, w in channels])[0]
        day = start + timedelta(days=rng.randrange(0, 180))
        items = max(1, int(rng.gauss(2.4, 1.3)))
        unit = rng.lognormvariate(3.3, 0.45) * region_lift * (1.12 if channel == "web" else 1.0)
        discount = rng.choice([0, 0, 0, 0.05, 0.1, 0.15, 0.2])
        amount = round(items * unit * (1 - discount), 2)
        returned = rng.random() < (0.06 + 0.25 * discount)
        rating = None if rng.random() < 0.18 else max(1, min(5, round(rng.gauss(4.2 - 2 * discount, 0.8))))
        customer = f"C{rng.randrange(1, 900):04d}"
        if rng.random() < 0.006:
            amount = -amount  # refunds mis-recorded as negative orders
        out.append({
            "order_id": f"O{i:05d}", "customer_id": customer, "order_date": day.isoformat(),
            "region": region, "channel": channel, "items": items, "discount": discount,
            "amount": amount, "returned": str(returned).lower(),
            "rating": "" if rating is None else rating,
        })
    out += [dict(r) for r in rng.sample(out, 12)]  # exact duplicate rows from a double export
    with (OUT / "ecommerce_orders.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out[0]))
        writer.writeheader()
        writer.writerows(out)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    ecommerce_orders()
