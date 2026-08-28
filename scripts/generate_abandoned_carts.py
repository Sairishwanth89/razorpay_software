import json
import time
from pathlib import Path

from app.razorpay_client import client

# Spread across the amount brackets the Guardrail/Learner care about (paise):
#   <=5k, 5k-25k, 25k-1L, >1L - so the demo batch exercises discount caps and
# escalation thresholds, not just one flat bucket. Left deliberately unpaid; the
# poller picks these up as abandoned once they clear ABANDONMENT_THRESHOLD_HOURS.
AMOUNT_PLAN = (
    [(round(45000 + i * 40000)) for i in range(10)]  # upto_5k: ~450-4050 rupees
    + [(round(700000 + i * 180000)) for i in range(10)]  # 5k-25k: ~7000-23200 rupees
    + [(round(3200000 + i * 900000)) for i in range(7)]  # 25k-1L: ~32000-86000 rupees
    + [(round(12000000 + i * 4000000)) for i in range(3)]  # above_1L: ~1.2L-2L
)

SCRIPTS_DIR = Path(__file__).parent


def build():
    orders = []
    for i, amount in enumerate(AMOUNT_PLAN):
        order = client.order.create(
            {
                "amount": amount,
                "currency": "INR",
                "notes": {"test_scenario": "abandoned_cart", "batch_index": str(i)},
            }
        )
        orders.append({"order_id": order["id"], "amount": amount, "index": i})
        print(f"[{i + 1}/{len(AMOUNT_PLAN)}] created abandoned order {order['id']} for INR {amount / 100:.2f}")
        time.sleep(0.5)

    (SCRIPTS_DIR / "abandoned_cart_orders.json").write_text(json.dumps(orders, indent=2), encoding="utf-8")
    print(f"\n{len(orders)} orders created and left unpaid.")


if __name__ == "__main__":
    build()
