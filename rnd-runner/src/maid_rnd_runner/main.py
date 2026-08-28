from __future__ import annotations
import argparse
from pathlib import Path
from maid_rnd_runner.harness import HarnessRunner

def main()->int:
    parser=argparse.ArgumentParser(description="MaidAI isolated R&D harness")
    parser.add_argument("--input",required=True);parser.add_argument("--source",required=True);parser.add_argument("--output",required=True);parser.add_argument("--cycle-id",required=True)
    parser.add_argument("--direct-workspace",action="store_true")
    parser.add_argument("--baseline-commit",default="")
    args=parser.parse_args();return HarnessRunner(
        Path(args.input),Path(args.source),Path(args.output),args.cycle_id,
        baseline_commit=args.baseline_commit,
    ).run()

if __name__=="__main__":raise SystemExit(main())
