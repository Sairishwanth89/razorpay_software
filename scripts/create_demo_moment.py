import sys

from app.razorpay_client import client

# For the demo video's "prove it's not staged" beat: one real Order, created live on
# camera, with a clean round amount for easy on-screen arithmetic. Reuses the same
# notes.customer_id convention as generate_abandoned_carts.py so the Trust Score /
# contact-slot machinery picks it up identically - it's just a batch of one.
AMOUNT = 99900  # Rs 999.00

CUSTOMER_ID = sys.argv[1] if len(sys.argv) > 1 else "cust_demo_video"

order = client.order.create(
    {
        "amount": AMOUNT,
        "currency": "INR",
        "notes": {"test_scenario": "demo_video_live_trigger", "customer_id": CUSTOMER_ID},
    }
)

print(f"Created order {order['id']} for Rs {AMOUNT / 100:.2f} (customer {CUSTOMER_ID})")
print(f"1. Show it's real: https://dashboard.razorpay.com/app/orders -> search {order['id']}")
print("2. It'll show up in Recovery Mesh within ~60-90s once the poller sweeps it (checks every 60s).")
