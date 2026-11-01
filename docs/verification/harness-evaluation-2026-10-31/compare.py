"""Reproduce both decisions from metadata, without model or candidate execution."""
import json,sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root.parents[2]/'stapled-factory/worker'))
from harness_evaluation import evaluate
pre=json.loads((root/'preregistration.json').read_text())
frozen=evaluate(pre,json.loads((root/'trials.json').read_text()))
reconciled=evaluate(pre,json.loads((root/'trials-contract-reconciled.json').read_text()))
reconciled.update(grade_version='as-written-contract-reconciliation-v1',promotion_authority=False)
print(json.dumps({'frozen':frozen,'contract_reconciled':reconciled},indent=2,sort_keys=True))
