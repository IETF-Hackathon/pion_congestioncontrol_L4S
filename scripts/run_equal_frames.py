#!/usr/bin/env python3
"""Run exactly two bounded Prague trials with one verified equal-size VP8 source.

Each trial uses its own fresh receiver, 30-second sender and 60-second monitor
capture via capture_testbed. An ambiguous or failed attempt stops this batch;
there are no retries, new competing flows or changes to shared network settings.
"""
import argparse
import copy
import datetime
import json
from pathlib import Path
import re
import sys

import capture_testbed
import sender
import source_manifest
import testbed

ROOT = Path(__file__).resolve().parents[1]


def execute(args):
    """Freeze the source/config/build and complete each captured trial sequentially."""
    config = testbed.load_config(ROOT/args.config)
    source = testbed.inspect_source(config)
    if config['controller'] != 'prague' or config['feedback'] != 'ccfb':
        raise ValueError('equal-frame trials require normal Prague with CCFB')
    if not source['constant_frame_size'] or source['keyframe_count'] != source['frames']:
        raise ValueError('expected equal-size, independently decodable keyframes')
    if config.get('source_expectation',{}).get('equal_frame_bytes') != source['largest_frame_bytes']:
        raise ValueError('config must enforce the measured equal frame size')
    if config['duration_seconds'] != 30:
        raise ValueError('this two-trial workflow uses bounded 30-second calls')
    sender.validate_target(args.host,args.remote_root)
    build = source_manifest.verify_build(ROOT/args.binary)
    tag = args.batch_id or 'pion-equalframes-25m-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,70}',tag):
        raise ValueError('invalid batch ID')
    base = ROOT/'results/raw'/tag
    if base.exists():
        raise ValueError('batch directory already exists; preserve it and use a new ID')
    base.mkdir(parents=True)
    frozen = base/'config.json';frozen.write_text(json.dumps(config,indent=2)+'\n')
    plan = dict(batch_id=tag,source=source,source_sha256=build['source_sha256'],
                receiver_binary_sha256=build['binary_sha256'],config=str(frozen.relative_to(ROOT)),
                remote_root=args.remote_root,sender_seconds=30,capture_seconds=60,
                original_config_sha256=testbed.sha256(ROOT/args.config),
                scope='Two identical-source Prague trials; fixed frame size, normal ACK/RTT/CE/loss/probing control',
                limitations=['Repeated static all-keyframe workload, not representative moving video',
                             'Actual source cadence can still fall under synchronous backpressure',
                             'Offered source bitrate differs from the historical variable-frame clip',
                             'Active qdisc/capacity and background traffic remain uncontrolled'],
                runs=[dict(run_id=tag+'-r'+str(i),repetition=i,controller='prague') for i in (1,2)])
    (base/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    progress = dict(status='prepared',completed=[])
    try:
        for trial in plan['runs']:
            if source_manifest.verify_build(ROOT/args.binary)['source_sha256'] != build['source_sha256']:
                raise ValueError('compiled source changed during the batch')
            if testbed.inspect_source(config)['sha256'] != source['sha256']:
                raise ValueError('media changed during the batch')
            current = copy.copy(args)
            current.config=str(frozen.relative_to(ROOT));current.run_id=trial['run_id']
            current.capture_seconds=60;current.collect_pcaps=True
            progress.update(status='running',active_run=current.run_id)
            (base/'progress.json').write_text(json.dumps(progress,indent=2)+'\n')
            print(json.dumps(dict(event='trial_starting',**trial)),flush=True)
            capture_testbed.run(current,config,source)
            progress['completed'].append(current.run_id)
            progress.update(status='between_trials',active_run=None)
            (base/'progress.json').write_text(json.dumps(progress,indent=2)+'\n')
        progress['status']='captured'
    except BaseException as error:
        progress.update(status='failed',error=str(error))
        raise
    finally:
        progress['original_config_preserved']=testbed.sha256(ROOT/args.config)==plan['original_config_sha256']
        (base/'progress.json').write_text(json.dumps(progress,indent=2)+'\n')
    print(json.dumps(dict(event='two_trials_captured',batch_id=tag,completed=progress['completed'])),flush=True)


def main():
    """Explicitly start the requested two trials using existing authenticated SSH."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--host',required=True)
    parser.add_argument('--remote-root',required=True);parser.add_argument('--control-path')
    parser.add_argument('--ssh-config',default='/dev/null');parser.add_argument('--go-bin')
    parser.add_argument('--binary',default='bin/pion-l4s');parser.add_argument('--batch-id')
    args=parser.parse_args()
    try:
        execute(args)
    except (OSError,ValueError,RuntimeError) as error:
        print('equal-frame-runs: '+str(error),file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__=='__main__':
    sys.exit(main())
