"""Offline comparison of every scheduled trial; never invokes a model."""
import collections,datetime,json,pathlib,statistics
out=pathlib.Path(__file__).resolve().parent
original=out.parent/'harness-baseline-2026-10-30';context=out.parent/'harness-context-paired-2026-10-30'

def load(root,schedule='execution-schedule.json'):
    return [json.loads((root/'trials'/(r['unit']+'.json')).read_text()) for r in json.loads((root/schedule).read_text())['runs']]
def gateway(root):return {r['unit']:r['gateway_logs'] for r in json.loads((root/'final-state-and-gateway.json').read_text())['trials']}
def routes(logs):return sorted(set((v.get('model')or'<unknown>',v.get('served_model')or v.get('model')or'<unknown>') for v in logs))
def lanes(logs):return sorted(set((v.get('provider'),v.get('model'),v.get('served_model')or v.get('model')) for v in logs))
def prompt(row):
    values=[row['telemetry'].get(k) for k in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens')]
    return sum(values) if all(type(v) is int for v in values) else None
def span(row):
    a=row['trace'].get('first_event_timestamp');b=row['trace'].get('last_event_timestamp')
    return (datetime.datetime.fromisoformat(b)-datetime.datetime.fromisoformat(a)).total_seconds() if a and b else None
def totals(rows,accept):
    fields={k:sum(r['telemetry'][k] for r in rows) if all(type(r['telemetry'].get(k)) is int for r in rows) else None for k in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens','output_tokens')}
    prompts=[prompt(r) for r in rows];p=sum(prompts) if all(v is not None for v in prompts) else None;n=sum(accept[r['unit']] for r in rows)
    req=sum(sum(v[3] for v in r['budget']['budgets']) for r in rows);spans=[span(r) for r in rows]
    return {'trials':len(rows),'accepted':n,'requests':req,'prompt_tokens':p,**fields,'prompt_tokens_per_accepted_task':p/n if p is not None and n else None,'requests_per_accepted_task':req/n if n else None,'cached_read_share_reported':fields['cache_read_input_tokens']/p if p and fields['cache_read_input_tokens'] is not None else None,'median_workflow_elapsed_s':statistics.median(r['task_wall_elapsed_s'] for r in rows),'median_agent_span_s':statistics.median(spans) if all(v is not None for v in spans) else None,'estimated_reserved_usd':sum(sum(v[-1] for v in r['budget']['monetary_budgets']) for r in rows)/1e6,'actual_charges_usd':None,'complete_responses':sum(r['telemetry']['complete_responses'] for r in rows),'telemetry_reservation_parity':all(r['reservation_count_matches_telemetry'] for r in rows)}
def lane_metrics(source):
    groups=collections.defaultdict(list)
    for logs in source.values():
        for row in logs:groups[(row['provider'],row['model'],row.get('served_model')or row['model'])].append(row)
    return [{'provider':k[0],'model':k[1],'served_model_or_reported_model':k[2],'requests':len(v),'prompt_tokens':sum(x['prompt_tokens'] for x in v) if all(type(x.get('prompt_tokens')) is int for x in v) else None} for k,v in sorted(groups.items())]
rows=load(out,'suite.json');assert len(rows)==18
current_gateway=gateway(out);accept={r['unit']:r['accepted'] for r in rows};after=totals(rows,accept)
comparisons={}
for name,root in [('original_baseline',original),('context_v1',context)]:
    old=load(root,'suite.json' if name=='context_v1' else 'execution-schedule.json');old_gateway=gateway(root)
    old_accept={r['unit']:r['accepted'] for r in old}
    if name=='original_baseline':old_accept={r['unit']:r['corrected_accepted'] for r in json.loads((root/'regrade-v2.json').read_text())['results']}
    old_by={(r['task_id'],r['repeat']):r for r in old};before=totals(old,old_accept);pairs=[]
    for row in rows:
        previous=old_by[row['task_id'],row['repeat']]
        assert all(row[k]==previous[k] for k in ('baseline','task_sha256','allowed_paths'))
        pairs.append({'task_id':row['task_id'],'repeat':row['repeat'],'reference_unit':previous['unit'],'candidate_unit':row['unit'],'reference_accepted':old_accept[previous['unit']],'candidate_accepted':row['accepted'],'reference_prompt_tokens':prompt(previous),'candidate_prompt_tokens':prompt(row),'reference_requests':sum(v[3] for v in previous['budget']['budgets']),'candidate_requests':sum(v[3] for v in row['budget']['budgets']),'actual_model_same':routes(old_gateway[previous['unit']])==routes(current_gateway[row['unit']]),'provider_lane_sets_same':lanes(old_gateway[previous['unit']])==lanes(current_gateway[row['unit']])})
    comparisons[name]={'reference':before,'candidate':after,'prompt_reduction_fraction':1-after['prompt_tokens_per_accepted_task']/before['prompt_tokens_per_accepted_task'] if after['prompt_tokens_per_accepted_task'] is not None and before['prompt_tokens_per_accepted_task'] is not None and before['prompt_tokens_per_accepted_task']>0 else None,'median_workflow_latency_change_fraction':after['median_workflow_elapsed_s']/before['median_workflow_elapsed_s']-1,'actual_model_same_all_pairs':all(p['actual_model_same'] for p in pairs),'same_provider_lane_sets_pairs':sum(p['provider_lane_sets_same'] for p in pairs),'pairs':pairs,'routing_lanes':{'reference':lane_metrics(old_gateway),'candidate':lane_metrics(current_gateway)}}
wire_complete=(after['complete_responses']==after['requests'] and
    all(type(after[k]) is int for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')) and
    all(r['telemetry'].get(k+'_coverage')==r['telemetry']['requests_observed'] for r in rows for k in ('input_tokens','output_tokens','cache_read_input_tokens','cache_creation_input_tokens')))
logs=[v for group in current_gateway.values() for v in group]
gateway_parity=all(v.get('status')=='success' for v in logs) and len(logs)==after['requests'] and all(type(v.get('prompt_tokens')) is int for v in logs) and sum(v['prompt_tokens'] for v in logs)==after['prompt_tokens']
checkpoint=all(r.get('checkpoint_evidence',{}).get('verified') is True and r['checkpoint_evidence'].get('read_after_namespace_stop') is True and r['checkpoint_evidence'].get('event_files',0)>0 for r in rows)
boundaries=all(r['resources_stopped'] and r['binding_restored'] and r['worker_telemetry_and_budget_paths_denied'] is True and r['trace'].get('complete') is True for r in rows)
canary=json.loads((out/'paid-canary.json').read_text());unpaid=json.loads((out/'unpaid-canary.json').read_text())
original_comparison=comparisons['original_baseline'];numeric=after['accepted']==18 and original_comparison['prompt_reduction_fraction'] is not None and original_comparison['prompt_reduction_fraction']>=.10 and original_comparison['median_workflow_latency_change_fraction']<=.10
safety=checkpoint and boundaries and wire_complete and gateway_parity and after['telemetry_reservation_parity'] and canary['accepted'] and unpaid['original_containers_stopped'] and unpaid['sdk_checkpoints_retained_after_stop'] and unpaid['model_requests']==0
models=all(v['actual_model_same_all_pairs'] for v in comparisons.values())
result={'schema':'factory.bounded-native-comparison/v1','profile':'context-v1 + bounded-native-v1','comparisons':comparisons,'gates':{'all_18_accepted':after['accepted']==18,'retained_checkpoints_after_stop':checkpoint,'boundaries_and_trace_and_restoration':boundaries,'wire_usage_complete':wire_complete,'gateway_wire_budget_parity':gateway_parity and after['telemetry_reservation_parity'],'unpaid_and_paid_canaries_passed':bool(canary['accepted'] and unpaid['model_requests']==0 and unpaid['original_containers_stopped']),'same_actual_model_identifiers':models,'numeric_thresholds_against_original_baseline':numeric,'scoped_pilot_gate_passed':bool(safety and models and numeric)},'prebinding_infrastructure_failures':5,'zero_call_candidate_replacements':4,'prebinding_failure_model_requests':0,'paid_canary_requests_separate':canary['requests'],'phase_9d_4_started':False,'whole_phase_9d_5_complete':False,'decision':'retain opt-in sandbox bounded profile; scoped pilot gates passed' if safety and models and numeric else 'hold broader rollout; one or more scoped pilot gates failed','limitations':['Historical, nonrandomized development pairs; six tasks with three repeats, not held-out or production acceptance','Unchanged auto router and matching reported model identifiers do not attest to identical weights; provider lanes differ','Combined context-v1 and containment profile; no attribution of additional token changes to batching','First4 model trials used645cb59, one useddb96ddd, one usedsecret-safe diagnostic hotfixeea52b0, remaining12 usedenvironment-order correction; identical worker image, prompt, schema, router and limits','Actual provider charges unknown; reservations are not a hard charge cap','Five pre-binding failures retained with zero gateway calls; replacement identities used only after signed no-dispatch proofs, without original redispatch or refund; proxy startup race and nondeterministic Docker environment ordering corrected; private network capacity reclaimed','Namespace containment trusts host operator, Docker and systemd; no daemon-loss or automatic reboot/remount recovery claim']}
(out/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'candidate':after,'gates':result['gates'],'reductions':{k:v['prompt_reduction_fraction'] for k,v in comparisons.items()}}))
