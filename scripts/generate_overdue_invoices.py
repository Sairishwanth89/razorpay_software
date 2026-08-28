import json
import time
from pathlib import Path

from app.razorpay_client import client

# Spread across the invoice_overdue brackets (paise): discount-eligible ceiling is
# 5,000,000 (Rs 50,000), escalate threshold is 10,000,000 (Rs 1,00,000).
AMOUNT_PLAN = (
    [round(80000 + i * 60000) for i in range(5)]  # upto_5k / 5k-25k mix: ~800-3200 rupees
    + [round(1500000 + i * 700000) for i in range(4)]  # 25k-1L: ~15000-36000 rupees
    + [round(6000000 + i * 3000000) for i in range(3)]  # above_1L (escalates immediately): 60000-1.2L rupees
)

# Real invoices, left unpaid with a short real expire_by so Razorpay itself expires them
# and fires a genuine invoice.expired webhook - no staged JSON, no browser needed.
EXPIRE_IN_SECONDS = 90

SCRIPTS_DIR = Path(__file__).parent


def build():
    invoices = []
    for i, amount in enumerate(AMOUNT_PLAN):
        invoice = client.invoice.create(
            {
                "type": "invoice",
                "description": f"Revenue recovery test invoice #{i}",
                "customer": {"name": "Test Customer", "contact": "9999999999", "email": "test@example.com"},
                "line_items": [
                    {"name": f"Test item {i}", "amount": amount, "currency": "INR", "quantity": 1}
                ],
                "sms_notify": 0,
                "email_notify": 0,
                "expire_by": int(time.time()) + EXPIRE_IN_SECONDS,
                "notes": {"test_scenario": "overdue_invoice", "batch_index": str(i)},
            }
        )
        invoices.append({"invoice_id": invoice["id"], "amount": amount, "index": i})
        print(f"[{i + 1}/{len(AMOUNT_PLAN)}] created invoice {invoice['id']} for INR {amount / 100:.2f}, expires in {EXPIRE_IN_SECONDS}s")
        time.sleep(0.5)

    (SCRIPTS_DIR / "overdue_invoices.json").write_text(json.dumps(invoices, indent=2), encoding="utf-8")
    print(f"\n{len(invoices)} invoices created, left unpaid. They'll expire for real in ~{EXPIRE_IN_SECONDS}s.")


if __name__ == "__main__":
    build()
