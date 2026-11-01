import collections,datetime,json,pathlib,statistics
out=pathlib.Path(__file__).resolve().parent
suite=json.loads((out/'execution-schedule.json').read_text())
rows=[json.loads((out/'trials'/(entry['unit']+'.json')).read_text()) for entry in suite['runs']]
assert len(rows)==18 and len({r['unit'] for r in rows})==18
assert all(r['baseline']==suite['baseline'] for r in rows)
accepted=sum(r['accepted'] for r in rows)
requests=sum(sum(row[3] for row in r['budget']['budgets']) for r in rows)
reserved=sum(sum(row[-1] for row in r['budget']['monetary_budgets']) for r in rows)
def total(field):
 values=[r['telemetry'].get(field) for r in rows]
 return sum(values) if all(type(v) is int for v in values) else None
spans=[]
for r in rows:
 a=r['trace'].get('first_event_timestamp');b=r['trace'].get('last_event_timestamp')
 if a and b:spans.append((datetime.datetime.fromisoformat(b)-datetime.datetime.fromisoformat(a)).total_seconds())
by_task=[]
for task in dict.fromkeys(r['task_id'] for r in rows):
 group=[r for r in rows if r['task_id']==task]
 by_task.append({'task_id':task,'trials':len(group),'accepted':sum(r['accepted'] for r in group),
 'requests':[sum(x[3] for x in r['budget']['budgets']) for r in group],
 'elapsed_s':[r['task_wall_elapsed_s'] for r in group],
 'wire_input_tokens':[r['telemetry']['input_tokens'] for r in group],
 'cache_read_input_tokens':[r['telemetry']['cache_read_input_tokens'] for r in group]})
summary={'schema':'factory.baseline-results/v1','release':suite['release'],'seed_baseline':suite['baseline'],
 'development_suite_sha256':suite['suite_sha256'],'actual_model_trials':18,'accepted_development_candidates':accepted,
 'pre_dispatch_infrastructure_failures':suite['pre_dispatch_failures'],
 'requests':requests,'estimated_reserved_usd':reserved/1e6,'actual_charges_usd':None,
 'wire_input_tokens':total('input_tokens'),'wire_output_tokens':total('output_tokens'),
 'wire_cache_read_input_tokens':total('cache_read_input_tokens'),'wire_cache_creation_input_tokens':total('cache_creation_input_tokens'),
 'requests_per_accepted_task':requests/accepted if accepted else None,
 'wire_input_tokens_per_accepted_task':total('input_tokens')/accepted if accepted and total('input_tokens') is not None else None,
 'median_task_observed_elapsed_s':statistics.median(r['task_wall_elapsed_s'] for r in rows),
 'median_agent_event_span_s':statistics.median(spans) if len(spans)==18 else None,
 'telemetry_reservation_parity_all_trials':all(r['reservation_count_matches_telemetry'] for r in rows),
 'complete_responses':sum(r['telemetry']['complete_responses'] for r in rows),
 'trace_complete_all_trials':all(r['trace']['complete'] for r in rows),
 'all_bound_resources_stopped':all(r['resources_stopped'] for r in rows),
 'all_service_bindings_restored':all(r['binding_restored'] for r in rows),
 'by_task':by_task,'phase_9d_2_started':False,
 'limitations':['Development tasks, not production acceptance or a vendor benchmark.',
 'Three repetitions per task; small sample and warm-cache/order effects.',
 'Wire input and cached-read fields are separate; any cache share is explicitly gateway-reported, not SDK-inferred or an actual billing claim.',
 'Observed workflow latency includes provisioning and reconciliation polling.',
 'One pre-credential network-pool failure was retained and replaced without reusing its identity.']}
gateway_path=out/'final-state-and-gateway.json'
if gateway_path.exists():
 gateway=json.loads(gateway_path.read_text());logs=[v for trial in gateway['trials'] for v in trial['gateway_logs']]
 assert len(logs)==requests
 def gateway_total(field):
  values=[v.get(field) for v in logs]
  return sum(values) if all(type(v) is int for v in values) else None
 prompt=gateway_total('prompt_tokens');cached=gateway_total('cached_read_tokens')
 summary.update(gateway_requests=len(logs),gateway_models=dict(collections.Counter(v['model'] for v in logs)),
  gateway_prompt_tokens=prompt,gateway_completion_tokens=gateway_total('completion_tokens'),
  gateway_cached_read_tokens=cached,
  gateway_reported_cached_read_share=cached/prompt if prompt and cached is not None else None,
  gateway_prompt_tokens_per_accepted_task=prompt/accepted if prompt is not None and accepted else None,
  gateway_and_wire_prompt_accounting_equal=(prompt==total('input_tokens')+total('cache_read_input_tokens')+total('cache_creation_input_tokens')) if all(total(x) is not None for x in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens')) else None,
  operator_active_marker_absent=gateway['operator_active_marker_absent'])
regrade_path=out/'regrade-v2.json'
if regrade_path.exists():
 audit=json.loads(regrade_path.read_text());n=audit['corrected_contract_passes']
 summary.update(frozen_checker_accepted_development_candidates=accepted,
  contract_corrected_accepted_development_candidates=n,acceptance_comparison_version='contract-corrected-v2',
  model_trials_rerun=0,corrected_requests_per_accepted_task=requests/n if n else None,
  corrected_wire_input_tokens_per_accepted_task=total('input_tokens')/n if n and total('input_tokens') is not None else None,
  corrected_gateway_prompt_tokens_per_accepted_task=summary.get('gateway_prompt_tokens')/n if n and summary.get('gateway_prompt_tokens') is not None else None)
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:summary[k] for k in ('actual_model_trials','accepted_development_candidates','contract_corrected_accepted_development_candidates','requests','estimated_reserved_usd','wire_input_tokens','wire_cache_read_input_tokens','median_task_observed_elapsed_s','telemetry_reservation_parity_all_trials')}))
