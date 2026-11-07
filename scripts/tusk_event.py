#!/usr/bin/env python3
"""Tester-side live reporting (one cheap shell line, optional). Writes to the agent's OWN dir, which is all the sandbox lets it write.

  tusk_event.py step "probing coupon stacking"        # what I am doing now (dashboard activity line)
  tusk_event.py check R-PRICE-02 pass|fail [note]     # I tested this rule and it held / was violated
Orchestrator (outside the sandbox):  tusk_event.py emit ACTOR TYPE key=value ...  [--root D]   -> .tusk/events.jsonl
"""
import json
import os
import sys
import time
from pathlib import Path


def orchestrator(argv):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tusklib import emit
    root = Path(os.getcwd())
    if "--root" in argv:
        i = argv.index("--root")
        root = Path(argv[i + 1])
        del argv[i:i + 2]
    emit(root, argv[0], argv[1], **dict(kv.split("=", 1) for kv in argv[2:] if "=" in kv))


def main():
    if sys.argv[1:2] == ["emit"]:
        return orchestrator(sys.argv[2:])
    if len(sys.argv) < 3 or sys.argv[1] not in ("step", "check"):
        sys.exit(__doc__)
    d = Path(os.environ.get("TUSK_AGENT_DIR") or os.getcwd())
    if sys.argv[1] == "step":
        ev = {"type": "step", "text": " ".join(sys.argv[2:])[:200]}
    else:
        ev = {"type": "check", "rule": sys.argv[2], "result": sys.argv[3] if len(sys.argv) > 3 else "pass", "note": " ".join(sys.argv[4:])[:200]}
    ev["ts"] = round(time.time(), 2)
    with open(d / "events.jsonl", "a") as f:
        f.write(json.dumps(ev) + "\n")


if __name__ == "__main__":
    main()
