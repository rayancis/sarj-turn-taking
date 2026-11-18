"""Audit an unchanged official run; add diagnostics without changing its scoring or evidence files."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ttbench.score import load_labels, score, agent_silences, find_yield, score_scenario, DEFAULT_THRESHOLDS

def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def distribution(values):
    values=sorted(values)
    return {'n':len(values),'median':statistics.median(values) if values else None,
            'p90':values[min(len(values)-1,int(.9*len(values)))] if values else None}

def verify(run_dir):
    run_dir=Path(run_dir)
    labels, manifest=load_labels(ROOT/'data/dev')
    meta=json.loads((run_dir/'run.json').read_text(encoding='utf-8'))
    logs=[json.loads(line) for line in (run_dir/'log.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    saved=json.loads((run_dir/'score.json').read_text(encoding='utf-8'))
    th={**DEFAULT_THRESHOLDS, **manifest['thresholds']}
    recomputed=score(labels,meta,logs,th)
    if recomputed != saved:
        raise RuntimeError('Official score does not match independent recomputation')
    latency=[]
    false_actions={}
    actions=[]
    for span in meta['scenarios']:
        sid=span['scenario_id']
        start, end=span['start_ms'], span['start_ms']+span['duration_ms']+meta.get('gap_ms',1500)
        decisions=[(r['stream_ms']-start,r['msg']['decision']) for r in logs
                   if r['dir']=='rx' and r['msg'].get('type')=='decision' and start<=r['stream_ms']<end]
        actions.extend({'scenario':sid,'at_ms':t,'decision':d} for t,d in decisions)
        silences,_=agent_silences(labels[sid],decisions,th)
        sr=score_scenario(labels[sid],decisions,th)
        for ev in labels[sid]['events']:
            if ev['kind']!='interruption': continue
            if ev['expected']=='STOP':
                y=find_yield(ev,silences,th)
                latency.append({'scenario':sid,'event_id':ev['event_id'],
                    'onset_ms':ev['onset_ms'],'evident_ms':ev['evident_ms'],
                    'stop_ms':y.start if y else None,
                    'from_onset_ms':y.start-ev['onset_ms'] if y else None,
                    'from_evident_signed_ms':y.start-ev['evident_ms'] if y else None,
                    'official_evident_latency_ms':max(0.,y.start-ev['evident_ms']) if y else None})
            if ev['expected']=='KEEP':
                out=next(r for r in sr['events'] if r['event_id']==ev['event_id'])
                if out['credit']==0:
                    for silence in silences:
                        if silence.start <= ev['offset_ms']+th['keep_tail_ms'] and silence.effective_end>ev['onset_ms']:
                            key=(sid,silence.start)
                            item=false_actions.setdefault(key,{'scenario':sid,'at_ms':silence.start,'failed_keep_events':[],'stray':False})
                            item['failed_keep_events'].append(ev['event_id'])
        for at in sr['protected']['spurious_stops']:
            key=(sid,at)
            false_actions.setdefault(key,{'scenario':sid,'at_ms':at,'failed_keep_events':[],'stray':False})['stray']=True
    bundle=ROOT/'demo/models/sarj_v3'
    paths=sorted(bundle.glob('*.py'))+sorted((bundle/'tt').glob('*.py'))+[bundle/'model-assets.json',bundle/'WHISPER_LICENSE']
    asset_spec=json.loads((bundle/'model-assets.json').read_text())
    asset_dir=bundle/'models/faster-whisper-tiny'
    actual_assets={name:sha(asset_dir/name) for name in asset_spec['files']}
    if actual_assets!=asset_spec['files']: raise RuntimeError('Asset mismatch')
    versions={line.split('==')[0]:importlib.metadata.version(line.split('==')[0])
              for line in (ROOT/'demo/requirements-public.txt').read_text().splitlines() if '==' in line and not line.startswith('#')}
    source_commit=meta.get('hello',{}).get('system_version')
    recorded_hashes={}
    current_hashes={p.relative_to(ROOT).as_posix():sha(p) for p in paths}
    for name in current_hashes:
        old=subprocess.run(['git','show',f'{source_commit}:{name}'],cwd=ROOT,capture_output=True)
        recorded_hashes[name]=hashlib.sha256(old.stdout).hexdigest() if old.returncode==0 else None
    return {
        'source_commit':meta.get('hello',{}).get('system_version'),
        'verification_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'core':saved['core'],'rates':saved['rates'],'protocol_errors':saved['protocol_errors'],
        'official_score_recomputed_exactly':True,
        'completed':meta['completed'],'speed':meta['speed'],'scenarios':len(meta['scenarios']),
        'frames_sent':meta['frames_sent'],'frames_planned':meta['frames_planned'],
        'max_send_lag_ms':meta['max_send_lag_ms'],'rtt_ms':saved['rtt_ms'],
        'stop_actions':sum(a['decision']=='STOP' for a in actions),
        'false_stop_scoring_events':saved['rates']['false_stops'],
        'false_stop_contributing_actions':len(false_actions),'false_actions':list(false_actions.values()),
        'latency_rows':latency,
        'latency_from_onset_ms':distribution([r['from_onset_ms'] for r in latency if r['stop_ms'] is not None]),
        'latency_from_evident_signed_ms':distribution([r['from_evident_signed_ms'] for r in latency if r['stop_ms'] is not None]),
        'official_evident_latency_ms':saved['latency_ms'],
        'source_sha256':recorded_hashes,'working_copy_source_sha256':current_hashes,
        'source_matches_current_candidate':recorded_hashes==current_hashes,
        'model_repository':asset_spec['repository'],'model_revision':asset_spec['revision'],'model_assets_sha256':actual_assets,
        'manifest_sha256':sha(ROOT/'data/dev/manifest.json'),
        'versions':versions,'python':sys.version,'os':platform.platform(),'cpu':platform.processor(),'logical_cpus':os.cpu_count(),
        'monotonic':vars(time.get_clock_info('monotonic')),
        'run_files_sha256':{name:sha(run_dir/name) for name in ('run.json','score.json','log.jsonl','report.md')},
        'false_action_definition':'Distinct silence-start STOP actions contributing to a failed KEEP window or a stray-stop item; not a new scoring rule.',
        'timing_warning':meta['max_send_lag_ms']>20,
    }

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('run',type=Path)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    result=verify(args.run)
    if not result['source_matches_current_candidate']:
        raise RuntimeError('The run source differs from the working candidate; remeasure it')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({k:result[k] for k in ('source_commit','core','rates','stop_actions','false_stop_contributing_actions',
        'latency_from_onset_ms','official_evident_latency_ms','max_send_lag_ms','protocol_errors')},indent=2))

if __name__=='__main__': main()
