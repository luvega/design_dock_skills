#!/usr/bin/env python3
"""Report local compute readiness without installing or changing anything."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from baker_design import load_yaml, preflight_environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--strict-run", action="store_true")
    args = parser.parse_args()
    try:
        result = preflight_environment(
            load_yaml(args.request), strict_run=args.strict_run
        )
    except ValueError as error:
        print(json.dumps({"ready_for_local_execution": False, "error": str(error)}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready_for_local_execution"] or not args.strict_run else 2


if __name__ == "__main__":
    raise SystemExit(main())
