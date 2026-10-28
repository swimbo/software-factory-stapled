"""CLI entry — run the wall evaluator and print a JSON verdict + receipt.

This is the seam the TypeScript Restate spine calls as a `ctx.run` effect:
  python3 wall/evaluator/cli.py --target http://127.0.0.1:8777 \
      --scenarios wall/holdout/scenarios.json --config wall/config.json --tier cheap
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import load_tiers  # noqa: E402
from evaluate import evaluate  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the holdout evaluator and print JSON.")
    ap.add_argument("--target", required=True)
    ap.add_argument("--scenarios", required=True)
    ap.add_argument("--config", default=None, help="tier config JSON")
    ap.add_argument("--tier", default=None, help="tier name (e.g. cheap/frontier)")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--pricing-mode", default="subscription", choices=["subscription", "metered"])
    ap.add_argument("--cost-usd", type=float, default=None)
    args = ap.parse_args()

    tier_config = None
    if args.config and args.tier:
        tier_config = load_tiers(args.config).get(args.tier)

    verdict, receipt = evaluate(
        args.target,
        args.scenarios,
        runs_per_scenario=args.runs,
        tier=args.tier or "unknown",
        pricing_mode=args.pricing_mode,
        cost_estimate_usd=args.cost_usd,
        tier_config=tier_config,
    )

    print(json.dumps({
        "verdict": {
            "passed": verdict.passed,
            "aggregate_pass_rate": verdict.aggregate_pass_rate,
            "failure_message": verdict.failure_message,
            "per_scenario": verdict.per_scenario,
        },
        "receipt": receipt.to_dict(),
    }))


if __name__ == "__main__":
    main()
