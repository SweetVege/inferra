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


def checkout_experiment(users: int = 24000) -> None:
    """A randomized checkout redesign test with a pre-period covariate, segments, and guardrails."""
    rng = random.Random(7)
    start = date(2026, 8, 3)
    devices = [("desktop", 0.42), ("mobile", 0.48), ("tablet", 0.10)]
    countries = [("US", 0.5), ("UK", 0.2), ("DE", 0.15), ("FR", 0.15)]
    out = []
    for i in range(1, users + 1):
        variant = "treatment" if rng.random() < 0.5 else "control"
        device = rng.choices([d for d, _ in devices], [w for _, w in devices])[0]
        country = rng.choices([c for c, _ in countries], [w for _, w in countries])[0]
        engagement = rng.lognormvariate(0, 0.9)
        pre_orders = max(0, min(20, round(rng.gauss(2.5 * engagement, 0.7))))
        pre_spend = round(sum(rng.lognormvariate(3.6, 0.5) for _ in range(pre_orders)), 2)
        base = 0.02 + 0.07 * min(engagement, 3.5) + (0.02 if device == "desktop" else 0)
        # The redesign helps on mobile, barely on desktop, and slightly slows page loads.
        lift = {"mobile": 1.16, "desktop": 1.03, "tablet": 1.08}[device] if variant == "treatment" else 1.0
        converted = rng.random() < min(0.95, base * lift)
        revenue = round(rng.lognormvariate(3.9, 0.5) * (0.7 + 0.25 * min(engagement, 4)), 2) if converted else 0.0
        latency = round(rng.gauss(820 if variant == "treatment" else 790, 140))
        out.append({
            "user_id": f"U{i:06d}", "assigned_on": (start + timedelta(days=rng.randrange(0, 21))).isoformat(),
            "variant": variant, "device": device, "country": country, "pre_orders": pre_orders,
            "pre_spend": pre_spend, "converted": int(converted), "revenue": revenue,
            "checkout_latency_ms": max(200, latency),
        })
    with (OUT / "checkout_experiment.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(out[0]))
        writer.writeheader()
        writer.writerows(out)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    ecommerce_orders()
    checkout_experiment()
