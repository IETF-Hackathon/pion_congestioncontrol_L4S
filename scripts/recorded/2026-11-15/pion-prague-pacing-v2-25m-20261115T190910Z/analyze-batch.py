#!/usr/bin/env python3
"""Compare three frozen Prague v2 captures with the saved three-trial GCC baseline.

Download captures only after every trial finishes. Keep raw SDP and traffic in
ignored results; publish aggregate measurements with their byte-basis limits.
"""
from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import os

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
PLAN = json.loads((BASE / 'plan.json').read_text())
PUBLIC = ROOT / 'results/preview'
STEM = PLAN['batch_id']
os.environ['MPLCONFIGDIR'] = str(BASE / 'mpl-cache')
spec = importlib.util.spec_from_file_location('pacing_capture_analysis', BASE / 'capture_analysis.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
import matplotlib.pyplot as plt
import numpy as np


def spread(rows, field):
    """Describe independent trial outcomes without pooling packet quantiles."""
    values = np.array([row[field] for row in rows], dtype=float)
    return dict(mean=float(values.mean()), sample_stddev=float(values.std(ddof=1)),
                minimum=float(values.min()), maximum=float(values.max()), n=len(values))


def augment(row):
    """Extract sender waiting and retain receiver shutdown outcomes separately."""
    folder = ROOT / 'results/raw' / row['run_id']
    state = row['sender_last_feedback_state']
    assert state['pacing_backend'] == 'linux-timerfd'
    assert not row['feedback_trace_truncated']
    row['scheduler_observations'] = {key: state.get(key) for key in (
        'pacing_backend', 'rate_bps', 'packet_window', 'packet_size', 'packet_burst',
        'burst_budget_bytes', 'burst_rounding_bytes', 'pacing_waits', 'window_waits',
        'pacing_wait_ns', 'window_wait_ns', 'max_wake_lateness_ns', 'late_wakes',
        'accepted_packets', 'accepted_accounted_bytes', 'write_failures', 'srtt_us')}
    samples = [entry for entry in capture.records(folder/'sender/endpoint.jsonl')
               if entry.get('msg') == 'sender metrics']
    row['sender_socket_series'] = [dict(time=entry['time'],
        udp_datagram_mbps=entry['media_udp']['interval_datagram_bps']/1e6,
        applied_target_mbps=entry['target_bps']/1e6,
        pacing_wait_ns=entry['prague']['pacing_wait_ns'],
        window_wait_ns=entry['prague']['window_wait_ns']) for entry in samples]
    row['receiver_errors'] = [dict(time=entry.get('time'), error=entry.get('error'))
        for entry in capture.records(folder/'receiver/endpoint.jsonl') if entry.get('level') == 'ERROR']
    log = (folder/'monitor/log.txt').read_text()
    row['capture_accounting'] = [line for line in log.splitlines()
                                if 'Packets received/dropped on interface' in line]


def plot(rows, gcc, old):
    """Show repeated frame rates and tails, separating the single historical run."""
    plt.rcParams.update({'font.family':'DejaVu Sans', 'font.size':11,
                         'axes.spines.top':False, 'axes.spines.right':False})
    fig, axes = plt.subplots(2, 2, figsize=(15, 9.5))
    for samples, label, color in ((gcc, 'Saved GCC · n=3', '#ad641f'),
                                  (rows, 'New Prague · n=3', '#176a91')):
        series = np.array([row['delivered_frame_mbps_per_second'] for row in samples])
        axes[0,0].plot(np.arange(30)+.5, series.mean(axis=0), color=color, label=label, lw=2)
        axes[0,0].fill_between(np.arange(30)+.5, series.min(axis=0), series.max(axis=0),
                               color=color, alpha=.12)
        x = [row['delivered_vp8_frame_mbps'] for row in samples]
        y = [row['transit_p99_ms'] for row in samples]
        axes[1,0].scatter(x, y, c=color, s=75, label=label)
    axes[0,0].plot(np.arange(30)+.5, old['delivered_frame_mbps_per_second'], color='#777777',
                   ls='--', label='Old Prague · one historical run')
    axes[0,0].set(xlabel='Seconds after first ingress media packet', ylabel='Delivered media frames (Mbps)',
                   title='Media rate: means and min–max bands', xlim=(0,30))
    axes[0,0].legend(fontsize=9)
    groups = ([old], rows, gcc)
    for i, (group, color) in enumerate(zip(groups, ('#888888','#176a91','#ad641f'))):
        values = np.array([row['steady_delivered_vp8_frame_mbps'] for row in group])
        axes[0,1].bar(i, values.mean(), color=color, alpha=.75, width=.6)
        axes[0,1].scatter(np.full(len(values),i), values, color='#222222', s=20, zorder=4)
        axes[0,1].annotate(f'{values.mean():.2f}', (i,values.max()), xytext=(0,8),
                           textcoords='offset points', ha='center')
    axes[0,1].set(xticks=[0,1,2], xticklabels=['Old Prague\nn=1','New Prague\nn=3','Saved GCC\nn=3'],
                   ylabel='Delivered frame Mbps, seconds 2–27', title='Same fixed source; repeated outcomes')
    axes[0,1].set_ylim(0, max(row['steady_delivered_vp8_frame_mbps'] for row in gcc+rows)*1.2)
    axes[1,0].scatter([old['delivered_vp8_frame_mbps']], [old['transit_p99_ms']], marker='x',
                       c='#777777', s=85, label='Old Prague · n=1')
    axes[1,0].set(xlabel='Whole-media delivered frame Mbps', ylabel='Per-trial P99 transit (ms)',
                   title='Throughput and transit, including fixed delay')
    axes[1,0].legend(fontsize=9)
    for i,(group,color) in enumerate(zip(groups,('#888888','#176a91','#ad641f'))):
        values = [row['missing_media_percent'] for row in group]
        axes[1,1].bar(i,np.mean(values),color=color,alpha=.75,width=.6)
        axes[1,1].scatter(np.full(len(values),i),values,color='#222222',s=20,zorder=4)
        axes[1,1].annotate(f'{np.mean(values):.3f}%',(i,max(values)),xytext=(0,8),
                           textcoords='offset points',ha='center')
    axes[1,1].set(xticks=[0,1,2],xticklabels=['Old Prague\nn=1','New Prague\nn=3','Saved GCC\nn=3'],
                   ylabel='Ingress RTP identities absent at egress (%)',title='Missing packets between the two taps')
    axes[1,1].set_ylim(0,max(row['missing_media_percent'] for row in gcc+rows)*1.25+.1)
    for axis in axes.flat: axis.grid(axis='y',alpha=.18)
    fig.suptitle('Prague pacing update against the saved GCC baseline',fontsize=19,x=.055,ha='left',y=.985)
    fig.text(.055,.94,'15 Nov 2026 · 23.95 Mbps VP8 source · 25 Mbps initial target · 30-second calls · independent 60-second captures',fontsize=10)
    fig.text(.055,.05,'Frame throughput includes packet headers. Shaded bands show trial range; dots are trials. Transit is not pure queue delay.',fontsize=10)
    fig.text(.055,.018,'Qdisc/capacity unverified; background traffic uncontrolled. Old Prague is one earlier observation. No adaptive encoder or causal superiority claim.',fontsize=9,color='#555555')
    fig.tight_layout(rect=(.035,.10,.995,.925),h_pad=2,w_pad=2)
    for suffix in ('png','svg'): fig.savefig(PUBLIC/(STEM+'.'+suffix),dpi=170)
    plt.close(fig)


def main():
    """Validate each captured trial, write reproducible summaries and compare cohorts."""
    progress = json.loads((BASE/'progress.json').read_text())
    if len(progress.get('completed',[])) != 3:
        raise RuntimeError('All three captures must finish before bulk download/analysis')
    analyzed = []
    for item in PLAN['runs']:
        row, _ = capture.analyze(item)
        row['repetition'] = item['repetition']
        assert row['process_outcomes']['sender']['binary_sha256'] == PLAN['sender_binary_sha256']
        assert row['process_outcomes']['receiver']['binary_sha256'] == PLAN['receiver_binary_sha256']
        assert row['process_outcomes']['sender']['video_sha256'] == PLAN['source']['sha256']
        assert row['process_outcomes']['sender']['exit_code'] == 0
        assert row['capture_start_attempts'] == 1 and not row['capture_partial']
        assert row['unpaired_egress_rtp_packets'] == 0
        assert row['egress_media_ecn_counts']['0'] == row['egress_media_ecn_counts']['2'] == 0
        assert row['egress_media_ecn_counts']['1']+row['egress_media_ecn_counts']['3'] == row['egress_rtp_packets']
        augment(row)
        (ROOT/'results/raw'/item['run_id']/'analysis.json').write_text(json.dumps(row,indent=2)+'\n')
        analyzed.append(row)
        print(json.dumps({key:row[key] for key in ('repetition','delivered_vp8_frame_mbps',
            'missing_media_percent','transit_p99_ms','new_ce_between_vantages','sender_processed_ce_last_feedback')}),flush=True)
    gcc_saved = json.loads((PUBLIC/(PLAN['baseline_id']+'.json')).read_text())
    gcc = gcc_saved['runs']
    assert gcc_saved['source']['sha256'] == PLAN['source']['sha256']
    old = next(row for row in json.loads((PUBLIC/'pion-prague-vs-gcc-25m-20261115T172333Z.json').read_text())['runs'] if row['controller']=='prague')
    measures=('delivered_vp8_frame_mbps','steady_delivered_vp8_frame_mbps',
              'monitor_udp_frame_mbps','missing_media_percent','transit_p50_ms','transit_p99_ms')
    stats={name:{field:spread(rows,field) for field in measures}
           for name,rows in (('new_prague',analyzed),('saved_gcc',gcc))}
    limits=[
        'Three new Prague trials are contrasted with three previously collected GCC trials, not a new interleaved randomized comparison.',
        'Same source hash, 25 Mbps initial target, hosts/interfaces, CCFB format, duration and steady window; binary changes are explicitly recorded.',
        'Frame throughput includes Ethernet/IP/UDP/SRTP overhead; it is not encoded-payload goodput. The steady window is seconds 2 through 27 after first ingress media.',
        'Transit includes fixed network/emulation delay; active DualPI2/SRM mode, capacity and pure queue delay remain unverified.',
        'No controlled competing traffic was generated. Existing background traffic was uncontrolled and can differ from the saved baseline.',
        'New sender metrics count UDP acceptance and pacing/window waits; UDP success is not a NIC departure timestamp. Saved GCC has no equivalent socket series.',
        'The source is a fixed prerecorded VP8 clip. Adaptive encoder targets, raw RTT variation and deduplicated application-payload goodput are unavailable.',
        'Receiver closed-pipe shutdown errors are retained separately from successful bounded sender completion; periodic receiver counters need not be final.',
        'Monitor CE / last recorded sender CE are 2213/2213, 2606/2599 and 2478/2476. The 7- and 2-mark tails have not been reconciled; the last feedback state is not final drained accounting.',
        'Capture diagnostics report one flushed interface packet per side per trial; zero unmatched media identities applies to the retained captures, not an independently measured physical loss guarantee.',
        'The >=25 Mbps unshaped actual-media acceptance gate remains incomplete; a 28.33 Mbps lab admission benchmark does not satisfy it.',
    ]
    result=dict(schema_version=1,comparison_id=STEM,scope=PLAN['scope'],plan=PLAN,runs=analyzed,
        saved_gcc_baseline=PLAN['baseline_id'],historical_old_prague=old['run_id'],statistics=stats,
        limitations=limits,unavailable_metrics=dict(pure_queue_delay_ms=None,raw_rtt_variation_ms=None,
        adaptive_encoder_target_bps=None,deduplicated_application_goodput_bps=None),
        requirement_status=dict(scheduler_implementation='implemented; unit/race/vet/build verified on macOS and Linux',
            source_and_capture_matching='verified, three new Prague trials',
            full_feedback_drain='partial, unresolved tails of 7 and 2 CE marks',
            current_qdisc_mode_capacity='unverified',remaining_window_limiter='observed waiting, root cause not isolated',
            unshaped_25mbps_actual_media_gate='not verified',adaptive_encoder='missing'),
        analysis_code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),BASE/'capture_analysis.py')})
    (PUBLIC/(STEM+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    fields=('controller','repetition','run_id','delivered_vp8_frame_mbps','steady_delivered_vp8_frame_mbps',
            'missing_media_percent','transit_p50_ms','transit_p99_ms','new_ce_between_vantages',
            'sender_processed_ce_last_feedback','dashboard_result_url')
    with (PUBLIC/(STEM+'.csv')).open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({field:row.get(field) for field in fields} for row in analyzed+gcc)
    plot(analyzed,gcc,old)
    lines=['# New Prague pacing versus saved GCC baseline','',
        'Three physical Prague/CCFB trials used the new frozen scheduler. Each 30-second call had a fresh receiver and a separate 60-second bottleneck capture.','',
        '| Condition | Trials | Steady frame Mbps, mean (range) | Missing RTP %, mean | Mean per-trial P99 transit ms |',
        '| --- | ---: | ---: | ---: | ---: |']
    for name,label in (('new_prague','New Prague'),('saved_gcc','Saved GCC')):
        s=stats[name]; t=s['steady_delivered_vp8_frame_mbps']
        lines.append(f"| {label} | 3 | {t['mean']:.3f} ({t['minimum']:.3f}–{t['maximum']:.3f}) | {s['missing_media_percent']['mean']:.3f} | {s['transit_p99_ms']['mean']:.3f} |")
    lines += ['',f"Historical old Prague: one earlier run, steady frame rate {old['steady_delivered_vp8_frame_mbps']:.3f} Mbps. It is not pooled into the new three-trial cohort.",'',
        f'![Pacing comparison]({STEM}.png)','',
        f'[Measurements and provenance]({STEM}.json) · [CSV]({STEM}.csv) · [Saved GCC baseline]({PLAN["baseline_id"]}.md)','',
        '## New trial details','',
        '| Trial | Steady frame Mbps | Monitor CE / sender processed CE | Pacing / window wait seconds, last feedback | Outcome |',
        '| --- | ---: | ---: | ---: | --- |']
    for row in analyzed:
        s=row['scheduler_observations']; p=row['process_outcomes']
        lines.append(f"| [{row['repetition']}]({row['dashboard_result_url']}) | {row['steady_delivered_vp8_frame_mbps']:.3f} | {row['new_ce_between_vantages']} / {row['sender_processed_ce_last_feedback']} | {s['pacing_wait_ns']/1e9:.3f} / {s['window_wait_ns']/1e9:.3f} | sender {p['sender']['exit_code']}; receiver {p['receiver']['exit_code']} |")
    lines += ['','Pacing/window waits are cumulative sender observations, not network latency. Receiver errors and capture-loss diagnostics are preserved in JSON.','',
        'The historical plateau was exceeded in all three new trials. GCC still delivered more frame throughput, while new Prague had lower observed transit tails and missing-packet counts. These separate-time cohorts do not isolate a controller-only effect.','',
        'The last feedback states show approximately 15.3–15.7 seconds of pacing waits and 12.3–12.7 seconds of window waits. Large-packet median ingress gaps are now 419–428 µs versus about 1,171 µs in the old Prague run. Timing improved; window/feedback interaction remains a material limiter to investigate. No native window or CCFB policy was changed during this batch.','',
        '## Interpretation and remaining limits','']+['- '+note for note in limits]+['',
        'Source and algorithm details: [three pacing formulas](../../docs/pion-l4s-interop/pacing-algorithms.md). The upstream UDP demo executable was not run; it remains an algorithm reference.','']
    (PUBLIC/(STEM+'.md')).write_text('\n'.join(lines))
    print(json.dumps({'statistics':stats,'plot':str(PUBLIC/(STEM+'.png'))},indent=2))


if __name__ == '__main__':
    main()
