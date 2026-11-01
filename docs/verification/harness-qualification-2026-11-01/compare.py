"""Reproduce the metadata comparison; never executes a worker or changes policy."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parents[2]/'stapled-factory/worker'))
from harness_evaluation import evaluate

if __name__ == '__main__':
    preregistration = json.loads((root/'preregistration.json').read_text())
    trials = json.loads((root/'trials.json').read_text())
    print(json.dumps(evaluate(preregistration, trials), indent=2, sort_keys=True))
