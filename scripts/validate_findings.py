#!/usr/bin/env python3
"""Validate finding files against resources/finding-schema.json. Invalid ones move to
findings_rejected/ with a .reason file so cheap-model junk never reaches replay.

  validate_findings.py [--root D]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import finding_files, finding_schema, jload, validate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    r = Path(ap.parse_args().root).resolve()
    schema, ok, bad = finding_schema(), 0, 0
    for f in finding_files(r):
        obj = jload(f)
        errs = ["not valid JSON"] if obj is None else validate(obj, schema)
        if not errs:
            ok += 1
            continue
        bad += 1
        rej = f.parent.parent / "findings_rejected"
        rej.mkdir(exist_ok=True)
        f.rename(rej / f.name)
        (rej / (f.name + ".reason")).write_text("\n".join(errs) + "\n")
        print(f"rejected {f.parent.parent.name}/{f.name}: {errs[0]}")
    print(f"valid={ok} rejected={bad}")


if __name__ == "__main__":
    main()
