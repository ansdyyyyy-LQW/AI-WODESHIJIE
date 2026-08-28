from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-test", action="store_true")
    args, _unknown = parser.parse_known_args()
    if args.self_test:
        from maid_ai_control.self_test import run_self_test
        result = run_self_test()
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    from maid_ai_control.app import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
