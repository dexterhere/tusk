#!/usr/bin/env python3
"""Validate finding files against resources/finding-schema.json. Invalid ones move to
.uat/rejected/<agent>/ with a .reason file so cheap-model junk never reaches replay.

  validate_findings.py [--root D]
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import config, emit, finding_files, finding_schema, jload, udir, validate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    r = Path(ap.parse_args().root).resolve()
    schema, ok, bad = finding_schema(), 0, 0
    cap, seen = config(r)["stop"]["max_findings_per_agent"], {}
    for f in finding_files(r):
        obj = jload(f)
        seen[f.parent.parent.name] = seen.get(f.parent.parent.name, 0) + 1
        errs = ["not valid JSON"] if obj is None else validate(obj, schema)
        if not errs and seen[f.parent.parent.name] > cap:
            errs = [f"over the per-agent limit of {cap} findings"]
        if not errs:
            ok += 1
            continue
        bad += 1
        rej = udir(r) / "rejected" / f.parent.parent.name      # outside the agent-writable dir
        rej.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(rej / f.name))
        (rej / (f.name + ".reason")).write_text("\n".join(errs) + "\n")
        print(f"rejected {f.parent.parent.name}/{f.name}: {errs[0]}")
    emit(r, "orchestrator", "validate", valid=ok, rejected=bad)
    print(f"valid={ok} rejected={bad}")


if __name__ == "__main__":
    main()
