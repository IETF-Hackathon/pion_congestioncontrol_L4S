"""Analyze three GCC-only repetitions without pooling them with older trials."""
from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import os
import time

BASE = Path(__file__).resolve().parent
ROOT = Path('/Users/albisht/ietf/pion-l4s-interop')
PLAN = json.loads((BASE / 'plan.json').read_text())
PUBLIC = ROOT / 'results/preview'
STEM = PLAN['batch_id']
os.environ['MPLCONFIGDIR'] = str(BASE / 'mpl-cache')
os.environ['XDG_CACHE_HOME'] = str(BASE / 'cache')

spec = importlib.util.spec_from_file_location('gcc_capture_analysis', BASE / 'capture_analysis.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
import matplotlib.pyplot as plt
import numpy as np

def spread(rows, field):
    values = np.array([row[field] for row in rows], dtype=float)
    return {'mean':float(values.mean()), 'sample_stddev':float(values.std(ddof=1)),
            'minimum':float(values.min()), 'maximum':float(values.max()), 'n':len(values)}

def main():
    deadline = time.monotonic() + 180
    while True:
        progress = json.loads((BASE / 'progress.json').read_text())
        if len(progress.get('completed', [])) == 3:
            break
        if progress.get('events') and progress['events'][-1].get('event') == 'comparison_failed':
            raise RuntimeError('Baseline coordinator failed; no bulk downloads started')
        if time.monotonic() > deadline:
            raise RuntimeError('All three captures must finish before bulk download and analysis')
        time.sleep(1)
    analyzed = []
    for item in PLAN['runs']:
        row, delays = capture.analyze(item)
        row['repetition'] = item['repetition']
        log = (ROOT / 'results/raw' / item['run_id'] / 'monitor/log.txt').read_text()
        row['capture_accounting'] = [line for line in log.splitlines()
                                     if 'Packets received/dropped on interface' in line]
        assert row['process_outcomes']['sender']['binary_sha256'] == PLAN['sender_binary_sha256']
        assert row['process_outcomes']['receiver']['binary_sha256'] == PLAN['receiver_binary_sha256']
        assert row['process_outcomes']['sender']['video_sha256'] == PLAN['source']['sha256']
        assert row['egress_media_ecn_counts']['0'] == row['egress_rtp_packets']
        assert row['egress_media_ecn_counts']['3'] == 0
        assert row['capture_start_attempts'] == 1 and not row['capture_partial']
        assert row['unpaired_egress_rtp_packets'] == 0
        assert not row['feedback_trace_truncated']
        analyzed.append((row, delays))
        print(json.dumps({'trial':row['repetition'], 'media_mbps':row['delivered_vp8_frame_mbps'],
                          'missing_percent':row['missing_media_percent'], 'p99_ms':row['transit_p99_ms']}), flush=True)
    rows = [row for row,_ in analyzed]
    measures = ('delivered_vp8_frame_mbps', 'steady_delivered_vp8_frame_mbps',
                'monitor_udp_frame_mbps', 'missing_media_percent', 'transit_p50_ms', 'transit_p99_ms')
    statistics = {field:spread(rows,field) for field in measures}
    notes = [
        'Three new GCC/CCFB trials, 30 seconds each, fresh receiver and separate 60-second monitor capture for every trial. Earlier GCC/Prague comparison runs are not pooled into these statistics.',
        'Identical frozen endpoint binaries from source commit '+PLAN['source_commit']+' and identical 23.950988 Mbps VP8 IVF source; initial controller target 25 Mbps in every trial.',
        'GCC only was selected. Its media was Non-ECT at the egress capture. CE=0 is therefore expected; sender CE is unavailable rather than evidence of an uncongested path.',
        'The pinned Pion GCC LeakyBucketPacer uses a 5 ms ticker and SetTargetBitrate applies a 1.5 multiplier to estimator updates. The plotted GCC bandwidth estimate is not a strict wire-rate cap; its pacer and packet overhead can produce a higher transmitted rate.',
        'Mean delivered media frame rate uses one client-side capture vantage and includes Ethernet/IP/UDP/SRTP headers. Monitor UDP frame averages use the monitor-defined flow interval and include control traffic, so their numerical values can differ.',
        'The common steady window is 2 through 27 seconds after each first ingress media packet. One-second delivered-rate plots use the first ingress media packet as time zero. Target-rate plots use approximately aligned one-second sender metrics.',
        'The endpoint limit is configured as 30 seconds; process shutdown and buffered-packet drain can extend the complete media interval. The plots show the first 30 seconds, whole-media rates use the actual captured interval, and steady comparisons use the common 25-second window.',
        'Missing media packets are unique VP8 RTP identities seen at NSI and absent at CMCI, divided by the ingress unique-media count. Monitor UDP drops also include non-media datagrams; controller loss estimates and end-to-end playback loss are separate quantities.',
        'Paired one-way transit includes fixed propagation/emulation delay and is not pure queue delay. P50 and P99 statistics below summarize per-trial quantiles, not a pooled packet quantile.',
        'Capture interface mapping was checked live: NSI=enp2s0 and CMCI=enp3s0. Active DualPI2/SRM mode, configured shaping capacity and queue telemetry were not independently verified.',
        'No controlled competing traffic was added. Existing lab and endpoint background traffic was uncontrolled. These are repeated fixed-source transport baselines, not completed adaptive-video performance validation.',
        'Bulk capture downloads were deferred until all three trials finished. Only this task\'s endpoint processes were stopped; the original local config and shared network settings were preserved.',
    ]
    result = {'schema_version':1, 'baseline_id':PLAN['batch_id'], 'scope':PLAN['scope'],
              'source_commit':PLAN['source_commit'], 'source':PLAN['source'],
              'receiver_binary_sha256':PLAN['receiver_binary_sha256'],
              'sender_binary_sha256':PLAN['sender_binary_sha256'], 'runs':rows,
              'gcc_pacer':{'implementation':'Pion LeakyBucketPacer, altanai/interceptor v0.1.51',
                           'tick_ms':5,'estimator_update_multiplier':1.5,
                           'evidence':'pinned pkg/gcc/leaky_bucket_pacer.go constructor and SetTargetBitrate'},
              'statistics':statistics, 'limitations':notes,
              'analysis_code_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__), BASE/'capture_analysis.py')},
              'requirement_status':{'repeated_GCC_transport_baseline':'verified, three trials',
                                    'matching_binary_and_source_provenance':'verified',
                                    'selected_media_at_both_monitor_sides':'verified',
                                    'live_qdisc_mode_and_capacity':'unverified',
                                    'adaptive_encoder_and_six_complete_metrics':'incomplete'},
              'unavailable_metrics':{'pure_queue_delay_ms':None,'deduplicated_application_goodput_bps':None,
                                     'adaptive_encoder_target_bps':None,'raw_rtt_variation_ms':None}}
    (PUBLIC/(STEM+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    fields = ['repetition','run_id','source_mbps','delivered_vp8_frame_mbps',
              'steady_delivered_vp8_frame_mbps','monitor_udp_frame_mbps','ingress_rtp_packets',
              'egress_rtp_packets','missing_unique_rtp_packets','missing_media_percent',
              'monitor_udp_drops','media_interval_seconds','transit_p50_ms','transit_p99_ms','dashboard_result_url']
    with (PUBLIC/(STEM+'.csv')).open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({field:row.get(field) for field in fields} for row in rows)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    figure,axes=plt.subplots(2,2,figsize=(15,9.3))
    colors=['#166b9d','#c35b16','#287a45']
    for (row,delays),color in zip(analyzed,colors):
        label='Trial '+str(row['repetition'])
        axes[0,0].plot(np.arange(30)+.5,row['delivered_frame_mbps_per_second'],label=label,color=color,lw=2)
        series=row['controller_target_series']
        axes[0,1].plot([point['elapsed_seconds'] for point in series],
                       [point['target_mbps'] for point in series],label=label,color=color,lw=2)
        ordered=np.sort(delays);indices=np.unique(np.linspace(0,len(ordered)-1,min(1500,len(ordered))).astype(int))
        axes[1,0].plot(ordered[indices],(indices+1)/len(ordered)*100,label=label,color=color,lw=2)
    for ax in axes[0]:
        ax.axhline(PLAN['source']['nominal_payload_bps']/1e6,color='#555555',ls='--',lw=1,label='Encoded source: 23.95 Mbps')
        ax.set_xlabel('Seconds into each trial');ax.set_ylabel('Mbps');ax.set_xlim(0,30);ax.legend(fontsize=9)
    axes[0,0].set_title('Delivered VP8 media frames at client side')
    axes[0,1].set_title('GCC bandwidth estimate (pacer multiplier: 1.5×)')
    axes[1,0].set_title('Transit across both monitor interfaces')
    axes[1,0].set_xlabel('One-way transit (ms; includes fixed delay)')
    axes[1,0].set_ylabel('Matched media packets (%)');axes[1,0].set_ylim(0,100);axes[1,0].legend(fontsize=9)
    bars=axes[1,1].bar([1,2,3],[row['missing_media_percent'] for row in rows],color=colors,width=.6)
    for bar,row in zip(bars,rows):
        axes[1,1].annotate(f"{row['missing_media_percent']:.2f}%\n{row['missing_unique_rtp_packets']:,} packets",
                         (bar.get_x()+bar.get_width()/2,bar.get_height()),xytext=(0,7),
                         textcoords='offset points',ha='center',fontsize=10)
    axes[1,1].set_xticks([1,2,3],['Trial 1','Trial 2','Trial 3'])
    axes[1,1].set_ylabel('Ingress media packets absent at client side (%)')
    axes[1,1].set_title('Media missing between capture sides')
    axes[1,1].set_ylim(0,max(row['missing_media_percent'] for row in rows)*1.3+.15)
    for ax in axes.flat:ax.grid(axis='y',alpha=.18)
    figure.suptitle('GCC baseline: three repeated ~25 Mbps source trials',fontsize=19,x=.06,ha='left',y=.985)
    figure.text(.06,.935,'15 Nov 2026 · 30 seconds per trial · CCFB · Linux .216 → lld-black4 → Mac .79 · identical frozen binaries and clip',fontsize=10.5,color='#444444')
    throughput=statistics['delivered_vp8_frame_mbps'];loss=statistics['missing_media_percent'];tail=statistics['transit_p99_ms']
    figure.text(.06,.06,f"Delivered media: mean {throughput['mean']:.2f} Mbps (range {throughput['minimum']:.2f}–{throughput['maximum']:.2f})  |  Missing media: {loss['minimum']:.2f}–{loss['maximum']:.2f}%  |  Per-trial P99 transit: {tail['minimum']:.1f}–{tail['maximum']:.1f} ms",fontsize=10)
    figure.text(.06,.018,'Fixed prerecorded source; no adaptive encoder. GCC media Non-ECT. Active qdisc/capacity unverified; background traffic uncontrolled.',fontsize=9,color='#555555')
    figure.tight_layout(rect=(.035,.105,.995,.92),h_pad=2.2,w_pad=2)
    figure.savefig(PUBLIC/(STEM+'.png'),dpi=170);figure.savefig(PUBLIC/(STEM+'.svg'));plt.close(figure)
    lines=['# GCC baseline: three repeated approximately 25 Mbps source trials','',
           'These trials provide a repeatable fixed-source transport reference while the Prague pacing ceiling is investigated.','',
           f"Source revision: `{PLAN['source_commit']}`. VP8: 1280×720, 60 fps, 23.950988 Mbps encoded payload, 25 Mbps initial controller target. All trials used GCC with RFC 8888 CCFB.",'',
           f"Source SHA-256: `{PLAN['source']['sha256']}`.",'',
           '| Trial | Delivered media Mbps | Steady media Mbps | Missing media | Transit P50 / P99 ms |',
           '| --- | ---: | ---: | ---: | ---: |']
    for row in rows:
        lines.append(f"| {row['repetition']} | {row['delivered_vp8_frame_mbps']:.3f} | {row['steady_delivered_vp8_frame_mbps']:.3f} | {row['missing_unique_rtp_packets']:,} ({row['missing_media_percent']:.3f}%) | {row['transit_p50_ms']:.3f} / {row['transit_p99_ms']:.3f} |")
    lines += ['',f"Across three trials, delivered media averaged **{throughput['mean']:.3f} Mbps**, sample standard deviation **{throughput['sample_stddev']:.3f} Mbps**, range **{throughput['minimum']:.3f}–{throughput['maximum']:.3f} Mbps**.",'',
              'Delivered media rates include packet headers. The steady window is seconds 2–27. Transit includes fixed delay. GCC CE was zero in all three captures because all selected media was Non-ECT; sender CE is unavailable.','',
              f'![GCC baseline]({STEM}.png)','',f'[Summary CSV]({STEM}.csv) · [Full measurements and provenance]({STEM}.json)','',
              '## Evidence and capture links','']
    for row in rows:
        lines.append(f"- Trial {row['repetition']}: [{row['run_id']}]({row['dashboard_result_url']}), started {row['sender_started_at_local']}. [Local packet and endpoint evidence](../raw/{row['run_id']}/analysis.json).")
    lines += ['','## Reusing the baseline','',
              'For a future Prague comparison, preserve this source hash, initial target, endpoint placement, feedback format, duration and analysis windows. Record the new binary revision and obtain current bottleneck settings. Repeat Prague three times and compare the distribution of outcomes rather than one favorable sample. The existing background traffic was not controlled, so these captures cannot establish a controller-only causal difference.','',
              '## Limits and remaining evidence','']
    lines += ['- '+note for note in notes]
    lines += ['','The GCC transport baseline is recorded. Full experiment completion still requires verified qdisc settings, adaptive source control, pure queue-delay and raw RTT measurements, application goodput, and controlled matched Prague trials.','']
    (PUBLIC/(STEM+'.md')).write_text('\n'.join(lines))
    print(json.dumps({'plot':str(PUBLIC/(STEM+'.png')),'statistics':statistics},indent=2))

if __name__=='__main__':
    main()
