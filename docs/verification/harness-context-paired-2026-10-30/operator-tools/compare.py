"""Offline comparison; never execute a worker or contact a provider."""
import collections,datetime,json,pathlib,random,statistics
out=pathlib.Path(__file__).resolve().parent
base=out.parent/'phase9d-telemetry-20261030'
schedule=json.loads((out/'suite.json').read_text());old_schedule=json.loads((base/'execution-schedule.json').read_text())
old_summary=json.loads((base/'summary.json').read_text());old_grade=json.loads((base/'regrade-v2.json').read_text())
old_accept={x['unit']:x['corrected_accepted'] for x in old_grade['results']}
old_rows={(x['task_id'],x['repeat']):json.loads((base/'trials'/(x['unit']+'.json')).read_text()) for x in old_schedule['runs']}
rows=[json.loads((out/'trials'/(x['unit']+'.json')).read_text()) for x in schedule['runs']]
assert len(rows)==18 and len({r['unit'] for r in rows})==18
old_gateway={r['unit']:r['gateway_logs'] for r in json.loads((base/'final-state-and-gateway.json').read_text())['trials']}
new_gateway={r['unit']:r['gateway_logs'] for r in json.loads((out/'final-state-and-gateway.json').read_text())['trials']}

def models(logs):return sorted(set((r.get('model') or '<unknown>',r.get('served_model') or r.get('model') or '<unknown>') for r in logs))
def lanes(logs):return sorted(set((r['provider'],r['model'],r.get('served_model') or r['model']) for r in logs))
def usage(r,k):return r['telemetry'].get(k)
def prompt(r):
 values=[usage(r,k) for k in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens')]
 return sum(values) if all(type(v) is int for v in values) else None
def span(r):
 a=r['trace'].get('first_event_timestamp');b=r['trace'].get('last_event_timestamp')
 return (datetime.datetime.fromisoformat(b)-datetime.datetime.fromisoformat(a)).total_seconds() if a and b else None
pairs=[]
for r in rows:
 b=old_rows[(r['task_id'],r['repeat'])]
 assert all(r[k]==b[k] for k in ('baseline','task_sha256','allowed_paths'))
 pairs.append({'task_id':r['task_id'],'repeat':r['repeat'],'baseline_unit':b['unit'],'candidate_unit':r['unit'],
 'baseline_accepted':old_accept[b['unit']],'candidate_accepted':r['accepted'],'baseline_prompt_tokens':prompt(b),'candidate_prompt_tokens':prompt(r),
 'baseline_requests':sum(v[3] for v in b['budget']['budgets']),'candidate_requests':sum(v[3] for v in r['budget']['budgets']),
 'baseline_elapsed_s':b['task_wall_elapsed_s'],'candidate_elapsed_s':r['task_wall_elapsed_s'],
 'baseline_agent_span_s':span(b),'candidate_agent_span_s':span(r),'actual_route_same':models(old_gateway[b['unit']])==models(new_gateway[r['unit']]),
 'baseline_routes':lanes(old_gateway[b['unit']]),'candidate_routes':lanes(new_gateway[r['unit']]),
 'exact_provider_lane_sets_same':lanes(old_gateway[b['unit']])==lanes(new_gateway[r['unit']])})

def totals(group,accept):
 n=sum(accept[r['unit']] for r in group);prompts=[prompt(r) for r in group]
 fields={k:sum(usage(r,k) for r in group) if all(type(usage(r,k)) is int for r in group) else None for k in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens','output_tokens')}
 p=sum(prompts) if all(v is not None for v in prompts) else None
 requests=sum(sum(v[3] for v in r['budget']['budgets']) for r in group)
 spans=[span(r) for r in group]
 return {'trials':len(group),'accepted':n,'requests':requests,'prompt_tokens':p,**fields,
 'prompt_tokens_per_accepted_task':p/n if p is not None and n else None,'requests_per_accepted_task':requests/n if n else None,
 'cached_read_share_reported':fields['cache_read_input_tokens']/p if p and fields['cache_read_input_tokens'] is not None else None,
 'median_workflow_elapsed_s':statistics.median(r['task_wall_elapsed_s'] for r in group),
 'median_agent_span_s':statistics.median(spans) if all(v is not None for v in spans) else None,
 'estimated_reserved_usd':sum(sum(v[-1] for v in r['budget']['monetary_budgets']) for r in group)/1e6,'actual_charges_usd':None,
 'complete_responses':sum(r['telemetry']['complete_responses'] for r in group),
 'telemetry_reservation_parity':all(r['reservation_count_matches_telemetry'] for r in group)}
before=totals(list(old_rows.values()),old_accept);after=totals(rows,{r['unit']:r['accepted'] for r in rows})
primary=1-after['prompt_tokens_per_accepted_task']/before['prompt_tokens_per_accepted_task'] if after['prompt_tokens_per_accepted_task'] is not None else None
latency=after['median_workflow_elapsed_s']/before['median_workflow_elapsed_s']-1
complete=after['complete_responses']==after['requests'] and after['prompt_tokens'] is not None
by_task=[]
for task in dict.fromkeys(p['task_id'] for p in pairs):
 g=[p for p in pairs if p['task_id']==task]
 by_task.append({'task_id':task,'baseline_accepted':sum(p['baseline_accepted'] for p in g),'candidate_accepted':sum(p['candidate_accepted'] for p in g),
 'baseline_requests':[p['baseline_requests'] for p in g],'candidate_requests':[p['candidate_requests'] for p in g],
 'baseline_prompt_tokens':sum(p['baseline_prompt_tokens'] for p in g),'candidate_prompt_tokens':sum(p['candidate_prompt_tokens'] for p in g) if all(p['candidate_prompt_tokens'] is not None for p in g) else None,
 'median_candidate_elapsed_s':statistics.median(p['candidate_elapsed_s'] for p in g)})
# Exploratory six-task cluster bootstrap, explicitly not an independent causal test.
bootstrap=[];rng=random.Random(20261030)
if all(p['candidate_prompt_tokens'] is not None for p in pairs):
 for _ in range(10000):
  sampled=[rng.choice(by_task) for _ in by_task];bn=sum(x['baseline_accepted'] for x in sampled);cn=sum(x['candidate_accepted'] for x in sampled)
  if bn and cn:bootstrap.append(1-(sum(x['candidate_prompt_tokens'] for x in sampled)/cn)/(sum(x['baseline_prompt_tokens'] for x in sampled)/bn))
bootstrap.sort();interval=[bootstrap[int(.025*len(bootstrap))],bootstrap[min(len(bootstrap)-1,int(.975*len(bootstrap)))]] if bootstrap else None
route_same=all(p['actual_route_same'] for p in pairs)
numeric=after['accepted']>=before['accepted'] and primary is not None and primary>=.10 and latency<=.10
boundaries=all(r['resources_stopped'] and r['binding_restored'] and r['worker_telemetry_and_budget_paths_denied'] for r in rows)
canary_passed=json.loads((out/'paid-canary.json').read_text())['accepted']
logs=[x for group in new_gateway.values() for x in group]
values=[x.get('prompt_tokens') for x in logs]
gateway_prompt=sum(values) if all(type(v) is int for v in values) else None
gateway_parity=len(logs)==after['requests'] and gateway_prompt is not None and gateway_prompt==after['prompt_tokens']
passed=numeric and complete and route_same and after['telemetry_reservation_parity'] and boundaries and canary_passed and gateway_parity
def lane_metrics(source):
 grouped=collections.defaultdict(list)
 for log in (x for logs in source.values() for x in logs):grouped[(log['provider'],log['model'],log.get('served_model') or log['model'])].append(log)
 result=[]
 for identity,logs in sorted(grouped.items()):
  def count(field):
   values=[x.get(field) for x in logs];return sum(values) if all(type(v) is int for v in values) else None
  result.append({'provider':identity[0],'model':identity[1],'served_model_or_reported_model':identity[2],'requests':len(logs),'prompt_tokens':count('prompt_tokens'),'completion_tokens':count('completion_tokens'),'cache_read_tokens':count('cached_read_tokens')})
 return result
result={'schema':'factory.context-paired-comparison/v1','profile':'context-v1','baseline_contract_version':'contract-corrected-v2','baseline_frozen_original_accepts':17,
 'primary_metric':'total_prompt_tokens_including_cache_per_contract_v2_accepted_task','baseline':before,'candidate':after,'primary_improvement_fraction':primary,
 'median_workflow_latency_change_fraction':latency,'exploratory_six_task_cluster_bootstrap_95_improvement_interval':interval,
 'actual_model_route_same_all_pairs':route_same,'all_trial_boundaries_and_restoration_passed':boundaries,'paid_canary_passed':canary_passed,'gateway_and_wire_prompt_and_request_accounting_match':gateway_parity,'candidate_complete_usage_all_requests':complete,'numeric_pilot_thresholds_passed':numeric,'scoped_pilot_gate_passed':passed,
 'exact_provider_lane_sets_same_all_pairs':all(p['exact_provider_lane_sets_same'] for p in pairs),
 'routing_lane_breakdown':{'baseline':lane_metrics(old_gateway),'candidate':lane_metrics(new_gateway)},
 'whole_phase_9d_5_complete':False,'phase_9d_3_started':False,
 'decision':'candidate meets scoped pilot thresholds; retain opt-in activation pending broader promotion gates and review provider-lane distributions' if passed else 'retain baseline; candidate does not satisfy every scoped pilot threshold',
 'by_task':by_task,'pairs':pairs,'limitations':['Historical paired run order, not randomized concurrent evaluation','One paid canary warmed candidate prefix; total prompt tokens is primary, avoiding uncached-only savings claims','Six development tasks and three repeats each, not private heldout or production acceptance','Combined profile; individual mechanism attribution was not evaluated','Actual billing unknown; all failed/exhausted trial consumption included','Gateway model identifiers are not independent model-weight attestations; high/low provider-lane distributions are reported separately']}
(out/'comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('primary_improvement_fraction','median_workflow_latency_change_fraction','numeric_pilot_thresholds_passed','decision')}))
