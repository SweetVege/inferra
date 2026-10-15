"""Generate the deterministic demo datasets in samples/. Run: python scripts/generate_samples.py"""

import csv
import math
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


def customer_churn(customers: int = 6000) -> None:
    """Subscription customers with a churn label driven by engagement, support, and billing issues."""
    rng = random.Random(11)
    plans = [("basic", 0.55, 29), ("pro", 0.35, 79), ("enterprise", 0.10, 249)]
    regions = ["NA", "EU", "APAC", "LATAM"]
    out = []
    for i in range(1, customers + 1):
        plan, _, price = rng.choices(plans, [w for _, w, _ in plans])[0]
        tenure = max(1, int(rng.expovariate(1 / 18)))
        logins = max(0, int(rng.gauss(14 if plan != "basic" else 9, 7)))
        tickets = min(12, int(rng.expovariate(1 / 1.1)))
        failures = rng.choices([0, 1, 2, 3], [0.82, 0.11, 0.05, 0.02])[0]
        discount = rng.random() < 0.25
        spend = round(price * (0.85 if discount else 1) * rng.uniform(0.95, 1.4), 2)
        nps = None if rng.random() < 0.3 else max(0, min(10, round(rng.gauss(7.5 - 0.4 * tickets, 2))))
        z = (
            -1.2 - 0.09 * logins + 0.32 * tickets + 0.9 * failures - 0.035 * tenure
            + {"basic": 0.4, "pro": 0.0, "enterprise": -0.8}[plan] - (0.15 if discount else 0)
        )
        churned = rng.random() < 1 / (1 + math.exp(-z))
        signup = date(2026, 9, 1) - timedelta(days=30 * tenure + rng.randrange(0, 30))
        out.append({
            "customer_id": f"C{i:05d}", "signup_date": signup.isoformat(), "plan": plan,
            "region": rng.choice(regions), "tenure_months": tenure, "monthly_spend": spend,
            "logins_30d": logins, "support_tickets_90d": tickets, "payment_failures_90d": failures,
            "has_discount": str(discount).lower(), "nps": "" if nps is None else nps, "churned": int(churned),
        })
    _write("customer_churn.csv", out)


def loyalty_program(stores: int = 60, weeks: int = 26, launch_week: int = 13) -> None:
    """Weekly store sales; 24 stores join a loyalty program at launch_week (difference-in-differences)."""
    rng = random.Random(23)
    start = date(2026, 1, 5)
    regions = ["North", "South", "East", "West"]
    treated = set(rng.sample(range(1, stores + 1), 24))
    out = []
    for s in range(1, stores + 1):
        base = rng.lognormvariate(10.3, 0.35)
        region = regions[s % 4]
        for w in range(weeks):
            season = 1 + 0.08 * math.sin(w / 4.5) + 0.004 * w
            effect = 1.06 if (s in treated and w >= launch_week) else 1.0
            sales = base * season * effect * rng.lognormvariate(0, 0.05)
            out.append({
                "store_id": f"S{s:03d}", "region": region, "week_start": (start + timedelta(weeks=w)).isoformat(),
                "in_program": int(s in treated), "weekly_sales": round(sales, 2),
            })
    _write("loyalty_program.csv", out)


def daily_sales(days: int = 540) -> None:
    """Daily orders and revenue with trend, weekly, and yearly seasonality."""
    rng = random.Random(31)
    start = date(2025, 3, 1)
    out = []
    for d in range(days):
        day = start + timedelta(days=d)
        weekly = [0.92, 0.95, 0.97, 1.0, 1.08, 1.18, 0.9][day.weekday()]
        yearly = 1 + 0.12 * math.sin(2 * math.pi * (day.timetuple().tm_yday - 80) / 365)
        orders = max(0, round(420 * (1 + 0.0007 * d) * weekly * yearly * rng.lognormvariate(0, 0.06)))
        out.append({"date": day.isoformat(), "orders": orders,
                    "revenue": round(orders * rng.lognormvariate(4.05, 0.03), 2)})
    _write("daily_sales.csv", out)


def daily_funnel() -> None:
    """Daily sessions and signups by platform, app version, channel, and country. An Android release
    (5.2) rolls out on 2026-09-21 with a checkout bug that cuts its conversion; paid traffic also grows."""
    rng = random.Random(41)
    start, days = date(2026, 6, 15), 119
    release = date(2026, 9, 21)
    platforms = {"ios": 0.36, "android": 0.40, "web": 0.24}
    channels = {"organic": (0.55, 0.062), "paid": (0.30, 0.041), "email": (0.15, 0.085)}
    countries = {"US": 0.5, "UK": 0.3, "DE": 0.2}
    platform_rate = {"ios": 1.1, "android": 1.0, "web": 0.9}
    out = []
    for d in range(days):
        day = start + timedelta(days=d)
        weekly = [1.0, 1.02, 1.0, 0.98, 0.95, 0.82, 0.85][day.weekday()]
        base_sessions = 9000 * weekly * (1 + 0.0015 * d)
        paid_boost = 1.25 if day >= release - timedelta(days=7) else 1.0
        rollout = min(1.0, max(0.0, (day - release).days / 5 + 0.2)) if day >= release else 0.0
        for platform, p_share in platforms.items():
            versions = {"ios": [("8.4", 1.0)], "web": [("web", 1.0)]}.get(
                platform, [("5.1", 1 - rollout), ("5.2", rollout)]
            )
            for version, v_share in versions:
                if v_share <= 0:
                    continue
                for channel, (c_share, c_rate) in channels.items():
                    c_weight = c_share * (paid_boost if channel == "paid" else 1.0)
                    for country, k_share in countries.items():
                        sessions = base_sessions * p_share * v_share * c_weight * k_share * rng.lognormvariate(0, 0.08)
                        sessions = max(1, round(sessions))
                        rate = c_rate * platform_rate[platform] * (0.55 if version == "5.2" else 1.0)
                        signups = sum(1 for _ in range(sessions) if rng.random() < rate)
                        out.append({
                            "date": day.isoformat(), "platform": platform, "app_version": version,
                            "channel": channel, "country": country, "sessions": sessions, "signups": signups,
                            "revenue": round(signups * rng.lognormvariate(3.4, 0.1), 2),
                        })
    _write("daily_funnel.csv", out)


def retail_order_lines() -> None:
    """A denormalized order-line export, the classic input for a star schema. Customer and product
    attributes repeat on every line. Planted issues: 4 customers moved region mid-period (so their
    region is not stable), and some orders are guest checkouts with no customer at all."""
    rng = random.Random(23)
    first = ["Ava", "Liam", "Mia", "Noah", "Zoe", "Ethan", "Ivy", "Lucas", "Ella", "Owen", "Nora", "Leo"]
    last = ["Chen", "Patel", "Garcia", "Kim", "Novak", "Silva", "Okafor", "Muller", "Rossi", "Haddad"]
    segments = [("consumer", 0.70), ("small_business", 0.22), ("enterprise", 0.08)]
    regions = ["North", "South", "East", "West"]
    customers = []
    for i in range(1, 501):
        customers.append({
            "customer_id": f"C{i:04d}",
            "customer_name": f"{rng.choice(first)} {rng.choice(last)}",
            "customer_segment": rng.choices([s for s, _ in segments], [w for _, w in segments])[0],
            "customer_region": rng.choice(regions),
            "customer_signup_date": (date(2024, 1, 1) + timedelta(days=rng.randrange(0, 720))).isoformat(),
        })
    movers = {c["customer_id"]: rng.choice([r for r in regions if r != c["customer_region"]])
              for c in rng.sample(customers, 4)}
    categories = {
        "Electronics": (["Headphones", "Charger", "Speaker", "Keyboard", "Webcam"], 4.1),
        "Home": (["Lamp", "Kettle", "Throw", "Planter", "Clock"], 3.5),
        "Apparel": (["Jacket", "Sneakers", "Hoodie", "Scarf", "Cap"], 3.6),
        "Beauty": (["Serum", "Cleanser", "Balm", "Mask", "Toner"], 2.9),
        "Sports": (["Yoga Mat", "Bottle", "Dumbbells", "Band Set", "Backpack"], 3.4),
    }
    brands = ["Northwind", "Acme", "Bluebird", "Kestrel", "Lumen", "Orbit"]
    adjectives = ["Classic", "Pro", "Lite", "Everyday", "Studio", "Trail"]
    products = []
    for n, (category, (nouns, price_mu)) in enumerate(categories.items()):
        for k in range(12):
            products.append({
                "product_sku": f"SKU-{1001 + n * 12 + k}",
                "product_name": f"{adjectives[k % 6]} {nouns[k % 5]}",
                "product_category": category,
                "product_brand": rng.choice(brands),
                "list_price": round(rng.lognormvariate(price_mu, 0.4), 2),
            })
    statuses = [("completed", 0.90), ("returned", 0.07), ("cancelled", 0.03)]
    channels = [("web", 0.5), ("app", 0.35), ("store", 0.15)]
    start, out = date(2026, 1, 1), []
    for o in range(1, 3001):
        day = start + timedelta(days=rng.randrange(0, 181))
        guest = rng.random() < 0.012
        customer = None if guest else rng.choice(customers)
        status = rng.choices([s for s, _ in statuses], [w for _, w in statuses])[0]
        channel = rng.choices([c for c, _ in channels], [w for _, w in channels])[0]
        for line in range(1, max(1, min(5, round(rng.gauss(2.1, 1.0)))) + 1):
            product = rng.choice(products)
            quantity = rng.choices([1, 2, 3], [0.7, 0.22, 0.08])[0]
            discount = rng.choice([0, 0, 0, 0.1, 0.15, 0.25])
            unit_price = round(product["list_price"] * (1 - discount), 2)
            row = {"order_id": f"SO{o:05d}", "line_number": line, "order_date": day.isoformat(),
                   "order_status": status, "channel": channel}
            if customer is None:
                row |= {k: "" for k in customers[0]}
            else:
                row |= customer
                if customer["customer_id"] in movers and day >= date(2026, 4, 1):
                    row["customer_region"] = movers[customer["customer_id"]]
            row |= {k: v for k, v in product.items() if k != "list_price"}
            row |= {"unit_list_price": product["list_price"], "quantity": quantity, "discount_pct": discount,
                    "unit_price": unit_price, "line_amount": round(unit_price * quantity, 2)}
            out.append(row)
    _write("retail_order_lines.csv", out)


def _write(name: str, rows: list[dict]) -> None:
    with (OUT / name).open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    ecommerce_orders()
    checkout_experiment()
    customer_churn()
    loyalty_program()
    daily_sales()
    daily_funnel()
    retail_order_lines()
