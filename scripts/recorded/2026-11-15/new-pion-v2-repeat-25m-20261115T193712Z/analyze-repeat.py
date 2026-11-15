#!/usr/bin/env python3
"""Identify and analyze one explicit repeat of system 2, the new Pion pacer.

Keep this repeat separate from the earlier three-trial cohort. Raw endpoint,
SDP and packet artifacts remain in the ignored run directory.
"""
from pathlib import Path
import csv
import hashlib
import importlib.util
import json
import statistics

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
PLAN = json.loads((BASE/'plan.json').read_text())
PUBLIC = ROOT/'results/preview'
spec = importlib.util.spec_from_file_location('repeat_capture_analysis', BASE/'capture_analysis.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


def main():
    """Require capture completion, verify the actual backend and publish measurements."""
    progress = json.loads((BASE/'progress.json').read_text())
    assert len(progress.get('completed',[])) == 1, 'Capture must finish before downloads'
    row, _ = capture.analyze(PLAN['runs'][0])
    assert row['process_outcomes']['sender']['exit_code'] == 0
    assert row['capture_start_attempts'] == 1 and not row['capture_partial']
    assert not row['feedback_trace_truncated']
    assert row['process_outcomes']['sender']['binary_sha256'] == PLAN['sender_binary_sha256']
    assert row['process_outcomes']['receiver']['binary_sha256'] == PLAN['receiver_binary_sha256']
    assert row['process_outcomes']['sender']['video_sha256'] == PLAN['source']['sha256']
    assert row['sender_last_feedback_state']['pacing_backend'] == 'linux-timerfd'
    gcc = json.loads((PUBLIC/(PLAN['baseline_id']+'.json')).read_text())
    prior = json.loads((PUBLIC/(PLAN['prior_new_pion_batch']+'.json')).read_text())
    fields=('Mean data rate (Mbps)','Packet Delay P50 (ms)','Packet Delay P99 (ms)')
    gcc_average={field:statistics.mean(float(r['monitor_downstream_summary'][field]) for r in gcc['runs']) for field in fields}
    prior_average={field:statistics.mean(float(r['monitor_downstream_summary'][field]) for r in prior['runs']) for field in fields}
    row['system']='2. New Pion: bounded elapsed-time byte credit and native 250 us burst sizing, Linux timerfd backend'
    row['baseline_monitor_means']=gcc_average
    row['prior_new_pion_monitor_means']=prior_average
    row['plan']=PLAN
    logs=capture.records(BASE/'receiver/endpoint.jsonl')
    row['receiver_errors']=[r for r in logs if r.get('level')=='ERROR']
    row['capture_accounting']=[line for line in (BASE/'monitor/log.txt').read_text().splitlines() if 'Packets received/dropped on interface' in line]
    row['limitations']=[
        'One fresh repeat, kept separate from the original three-trial New Pion cohort and the earlier three GCC trials.',
        'Same frozen binary, media hash, initial target, endpoint placement, CCFB and duration as the prior New Pion trials. New run and capture IDs.',
        'Monitor throughput includes headers and control; paired VP8 frame throughput uses a different interval. Neither is decoded video or application-payload goodput.',
        'Transit includes fixed delay. Active qdisc/capacity remain unverified and background traffic uncontrolled.',
        'Capture CE and last sender-processed CE are reported separately; the last state is not necessarily final drained accounting.',
        'Fixed source, no adaptive encoder. No raw RTT distribution or pure queue-delay claim.',
    ]
    row['analysis_code_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (Path(__file__),BASE/'capture_analysis.py')}
    stem=PLAN['batch_id']
    (PUBLIC/(stem+'.json')).write_text(json.dumps(row,indent=2)+'\n')
    summary=row['monitor_downstream_summary']; state=row['sender_last_feedback_state']
    columns=('condition','trials','monitor_frame_mbps','median_transit_ms','p99_transit_ms')
    comparison=[dict(condition='New Pion fresh repeat',trials=1,monitor_frame_mbps=float(summary[fields[0]]),median_transit_ms=float(summary[fields[1]]),p99_transit_ms=float(summary[fields[2]]))]
    for name,values in (('Prior New Pion',prior_average),('Saved GCC',gcc_average)):
        comparison.append(dict(condition=name,trials=3,monitor_frame_mbps=values[fields[0]],median_transit_ms=values[fields[1]],p99_transit_ms=values[fields[2]]))
    with (PUBLIC/(stem+'.csv')).open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(comparison)
    lines=['# Explicit repeat: system 2, New Pion','',
        'This fresh physical call used the same frozen new scheduler as the previous three Prague trials: bounded byte credit, native 250 µs burst sizing and the Linux timerfd backend. It did not run the archived per-packet scheduler or the standalone UDP Prague demo.','',
        f"Sender binary SHA-256: `{PLAN['sender_binary_sha256']}`. Source archive SHA-256: `{PLAN['source_archive_sha256']}`.",'',
        '| Condition | Trials | Monitor frame Mbps | Median transit ms | P99 transit ms |',
        '| --- | ---: | ---: | ---: | ---: |']
    for item in comparison:
        lines.append(f"| {item['condition']} | {item['trials']} | {item['monitor_frame_mbps']:.3f} | {item['median_transit_ms']:.3f} | {item['p99_transit_ms']:.3f} |")
    lines += ['',
        f"Fresh repeat steady VP8 frame rate: **{row['steady_delivered_vp8_frame_mbps']:.3f} Mbps** over seconds 2–27, including headers. Missing RTP identities between taps: **{row['missing_unique_rtp_packets']} ({row['missing_media_percent']:.3f}%)**.",'',
        f"CE at capture / last sender-processed CE: **{row['new_ce_between_vantages']} / {row['sender_processed_ce_last_feedback']}**. Cumulative pacing/window waits at the last feedback state: **{state['pacing_wait_ns']/1e9:.3f} / {state['window_wait_ns']/1e9:.3f} seconds**.",'',
        f"Sender outcome: `{row['process_outcomes']['sender']}`. Receiver outcome: `{row['process_outcomes']['receiver']}`.",'',
        f"[Dashboard result]({row['dashboard_result_url']}) · [Downstream flow plot]({row['flow_plot_url']}) · [JSON]({stem}.json) · [CSV]({stem}.csv)",'',
        '## Limits','']+['- '+note for note in row['limitations']]+['']
    (PUBLIC/(stem+'.md')).write_text('\n'.join(lines))
    print(json.dumps({'run':stem,'backend':state['pacing_backend'],'comparison':comparison,'steady_frame_mbps':row['steady_delivered_vp8_frame_mbps'],'missing_rtp':row['missing_unique_rtp_packets'],'capture_ce':row['new_ce_between_vantages'],'sender_ce':row['sender_processed_ce_last_feedback'],'result_url':row['dashboard_result_url'],'flow_plot_url':row['flow_plot_url']},indent=2))


if __name__=='__main__':
    main()
