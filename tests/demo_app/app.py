"""Tiny shop API with 5 seeded business-logic bugs, for calibrating UAT testers. Stdlib only.

  python3 app.py PORT
Rules (the oracle) are in rules.json; the bugs are listed in seeded_bugs.json.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRICES = {"A": 10.00, "B": 0.10}
S = {}


def reset():
    S.clear()
    S.update(carts={}, orders={}, coupons={}, next_id=1)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if self.path == "/":
            return self.send(200, {"ok": True})
        if self.path.startswith("/orders/"):
            o = S["orders"].get(int(self.path.rsplit("/", 1)[1]))
            if not o:
                return self.send(404, {"error": "no order"})
            # BUG R-AUTH-01: never checks that X-User owns the order
            return self.send(200, o)
        self.send(404, {})

    def do_POST(self):
        b = self.body()
        if self.path == "/reset":
            reset()
            return self.send(200, {"ok": True})
        user = b.get("user", "anon")
        if self.path == "/cart/add":
            qty = b.get("qty", 1)
            if b.get("sku") not in PRICES:
                return self.send(400, {"error": "unknown sku"})
            # BUG R-CART-01: negative / zero quantity accepted
            S["carts"].setdefault(user, []).append((b["sku"], qty))
            return self.send(200, {"ok": True})
        if self.path == "/cart/coupon":
            if b.get("code") == "SAVE10":
                # BUG R-PRICE-02: coupon stacks when applied twice
                S["coupons"][user] = S["coupons"].get(user, 0) + 1
                return self.send(200, {"ok": True})
            return self.send(400, {"error": "bad code"})
        if self.path == "/checkout":
            cart = S["carts"].pop(user, [])
            # BUG R-ORDER-01: empty cart is not rejected
            total = sum(PRICES[s] * q for s, q in cart)
            for _ in range(S["coupons"].pop(user, 0)):
                total *= 0.9
            # BUG R-PRICE-01: total not rounded to cents
            oid = S["next_id"]
            S["next_id"] += 1
            S["orders"][oid] = {"id": oid, "user": user, "total": total}
            return self.send(200, S["orders"][oid])
        self.send(404, {})


if __name__ == "__main__":
    reset()
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
