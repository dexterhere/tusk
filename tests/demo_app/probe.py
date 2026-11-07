"""Repro helper for the demo app: `probe.py CHECK`. Asserts CORRECT behaviour; exit 1 = bug present."""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("TUSK_BASE_URL", "http://127.0.0.1:8080")


def call(method, path, body=None, user=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body or {}).encode() if method == "POST" else None)
    if user:
        req.add_header("X-User", user)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def check(name):
    call("POST", "/reset")
    if name == "neg_qty":
        return call("POST", "/cart/add", {"user": "u", "sku": "A", "qty": -2})[0] == 400
    if name == "double_coupon":
        call("POST", "/cart/add", {"user": "u", "sku": "A", "qty": 1})
        call("POST", "/cart/coupon", {"user": "u", "code": "SAVE10"})
        call("POST", "/cart/coupon", {"user": "u", "code": "SAVE10"})
        return call("POST", "/checkout", {"user": "u"})[1].get("total") == 9.0
    if name == "empty_checkout":
        return call("POST", "/checkout", {"user": "u"})[0] == 400
    if name == "float_total":
        call("POST", "/cart/add", {"user": "u", "sku": "B", "qty": 3})
        return call("POST", "/checkout", {"user": "u"})[1].get("total") == 0.3
    if name == "idor":
        call("POST", "/cart/add", {"user": "alice", "sku": "A", "qty": 1})
        oid = call("POST", "/checkout", {"user": "alice"})[1]["id"]
        return call("GET", f"/orders/{oid}", user="bob")[0] in (401, 403, 404)
    if name == "cart_isolation":  # decoy: holds, so replay must say not_reproducible
        call("POST", "/cart/add", {"user": "alice", "sku": "A", "qty": 1})
        return call("POST", "/checkout", {"user": "bob"})[1].get("total", 0) == 0
    raise SystemExit(f"unknown check {name}")


if __name__ == "__main__":
    sys.exit(0 if check(sys.argv[1]) else 1)
