import json
import time
from pathlib import Path

from app.config import settings
from app.razorpay_client import client

SCENARIOS = [
    {
        "name": "payment_timed_out",
        "error_code": "payment_timed_out",
        "description": "Payment could not be completed due to a temporary issue",
        "card": "4100280000090000",
        "amount": 45000,
    },
    {
        "name": "insufficient_fund",
        "error_code": "insufficient_fund",
        "description": "Payment could not be completed due to insufficient account balance",
        "card": "4100280000080001",
        "amount": 120000,
    },
    {
        "name": "payment_cancelled",
        "error_code": "payment_cancelled",
        "description": "Payment has been cancelled",
        "card": "4100280000070002",
        "amount": 350000,
    },
    {
        "name": "card_declined",
        "error_code": "card_declined",
        "description": "Payment did not go through as it was declined by the bank",
        "card": "4100280000060003",
        "amount": 499900,
    },
    {
        "name": "card_disabled_for_online_payments",
        "error_code": "card_disabled_for_online_payments",
        "description": "Card is disabled for online payments",
        "card": "4100280000030006",
        "amount": 875000,
    },
    {
        "name": "card_number_invalid",
        "error_code": "card_number_invalid",
        "description": "Incorrect card number entered",
        "card": "4100280000010008",
        "amount": 1500000,
    },
    {
        "name": "gateway_technical_error",
        "error_code": "gateway_technical_error",
        "description": "Payment did not go through due to a temporary gateway issue",
        "card": "4100280000020007",
        "amount": 2200000,
    },
    {
        "name": "authentication_failed",
        "error_code": "authentication_failed",
        "description": "Payment could not be completed due to incorrect OTP or verification details",
        "card": "4100280000000009",
        "amount": 3150000,
    },
]

SCRIPTS_DIR = Path(__file__).parent
SERVE_DIR = SCRIPTS_DIR / "serve"
SERVE_DIR.mkdir(exist_ok=True)

template = (SCRIPTS_DIR / "checkout_template.html").read_text(encoding="utf-8")


def build():
    orders = []
    for i, scenario in enumerate(SCENARIOS):
        order = client.order.create(
            {
                "amount": scenario["amount"],
                "currency": "INR",
                "notes": {"test_scenario": scenario["name"]},
            }
        )
        orders.append({**scenario, "order_id": order["id"], "index": i})
        print(f"created order {order['id']} for scenario {scenario['name']}")
        time.sleep(1.2)

        html = (
            template.replace("__SCENARIO_NAME__", scenario["name"])
            .replace("__ERROR_CODE__", scenario["error_code"])
            .replace("__DESCRIPTION__", scenario["description"])
            .replace("__CARD_NUMBER__", scenario["card"])
            .replace("__KEY_ID__", settings.razorpay_key_id)
            .replace("__AMOUNT__", str(order["amount"]))
            .replace("__CURRENCY__", order["currency"])
            .replace("__ORDER_ID__", order["id"])
        )
        (SERVE_DIR / f"checkout_{i}.html").write_text(html, encoding="utf-8")

    (SCRIPTS_DIR / "batch_orders.json").write_text(json.dumps(orders, indent=2), encoding="utf-8")
    write_index(orders)


def write_index(orders):

    rows = "\n".join(
        f"<tr><td>{o['name']}</td><td>{o['error_code']}</td>"
        f"<td>₹{o['amount'] / 100:.2f}</td><td>{o['order_id']}</td>"
        f"<td><a href='checkout_{o['index']}.html' target='_blank'>Open &rarr;</a></td></tr>"
        for o in orders
    )
    index_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Test Batch</title>
<style>
body {{ font-family: sans-serif; max-width: 800px; margin: 40px auto; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border: 1px solid #ccc; padding: 8px 12px; text-align: left; }}
</style></head>
<body>
<h2>Revenue Recovery Test Batch — {len(orders)} scenarios</h2>
<p>Click "Open" for each row, complete the checkout with the pre-filled card details shown on that page.
Each one fires a real payment.failed webhook to the local receiver.</p>
<table>
<tr><th>Scenario</th><th>Error code</th><th>Amount</th><th>Order ID</th><th></th></tr>
{rows}
</table>
</body></html>
"""
    (SERVE_DIR / "index.html").write_text(index_html, encoding="utf-8")
    print(f"\n{len(orders)} orders. Serve directory: {SERVE_DIR}")


if __name__ == "__main__":
    build()
