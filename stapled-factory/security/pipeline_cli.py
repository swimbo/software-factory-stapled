"""CLI — run the deterministic security pipeline on a diff and print JSON.

Usage:
  git diff | python3 security/pipeline_cli.py --paths src/auth.py src/crypto.py
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import scan  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the deterministic security pipeline on a diff.")
    ap.add_argument("--diff-file", default=None, help="path to a diff file (default: read stdin)")
    ap.add_argument("--paths", nargs="*", default=[], help="changed file paths (for relevance classification)")
    ap.add_argument("--target", default=None, help="explicit target dir for Semgrep/Gitleaks")
    ap.add_argument("--lockfile", default=None, help="lockfile for OSV-Scanner (e.g. package-lock.json)")
    args = ap.parse_args()

    diff_text = Path(args.diff_file).read_text() if args.diff_file else sys.stdin.read()
    report = scan(diff_text, args.paths, target=args.target, lockfile=args.lockfile)
    print(json.dumps(report.to_dict()))


if __name__ == "__main__":
    main()
