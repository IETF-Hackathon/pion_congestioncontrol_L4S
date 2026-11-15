#!/usr/bin/env python3
"""Derive the Prague throughput RCA from retained sender and capture evidence.

This script reads local artifacts and runs an explicitly simplified discrete
event model. It starts no endpoints, captures, contention or network changes.
"""
from pathlib import Path
import datetime
import hashlib
import heapq
import json
import math
import os
import re
import statistics

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[2]
PUBLIC = ROOT/'results/preview'
os.environ['MPLCONFIGDIR'] = str(BASE/'mpl-cache')
os.environ['XDG_CACHE_HOME'] = str(BASE/'cache')


def read_jsonl(path):
    """Read structured records while ignoring plain diagnostic log lines."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]


def stamp(text):
    """Normalize Go nanoseconds for Python's microsecond-resolution UTC parser."""
    return datetime.datetime.fromisoformat(re.sub(r'(\.\d{6})\d+',r'\1',text).replace('Z','+00:00')).timestamp()


def describe(values):
    """Summarize observed samples without treating them as time-weighted data."""
    import numpy as np
    return dict(n=len(values),minimum=min(values),median=statistics.median(values),
                p95=float(np.percentile(values,95)),maximum=max(values),mean=statistics.mean(values))


def sender_evidence(run_id):
    """Align cumulative sender counter deltas and inspect actual CCFB arrivals."""
    folder=ROOT/'results/raw'/run_id
    log=read_jsonl(folder/'sender/endpoint.jsonl')
    samples=[row for row in log if row.get('msg')=='sender metrics']
    first,last=samples[0],samples[-1]
    seconds=stamp(last['time'])-stamp(first['time'])
    def delta(group,key):
        """Subtract the same cumulative counter at the two sender snapshots."""
        return last[group][key]-first[group][key]
    pacing=delta('prague','pacing_wait_ns')/1e9
    window=delta('prague','window_wait_ns')/1e9
    trace=read_jsonl(folder/'sender/recording/session-trace.jsonl')
    feedback=[row for row in trace if row.get('event')=='controller_feedback'
              and any('CCFeedbackReport' in name for name in row['data']['packet_types'])]
    before=[row['data']['state_before_dispatch'] for row in feedback]
    after=[row['data']['state_after_dispatch'] for row in feedback]
    near_full=sum(row['packet_window']*row['packet_size']-row['inflight_bytes']<row['burst_budget_bytes'] for row in before)
    releases=sum(a['inflight_bytes']>b['inflight_bytes'] for a,b in zip(before,after))
    offset_ms=[];seen=set()
    for row in feedback:
        for packet in row['data']['packets']:
            for block in packet.get('ReportBlocks',[]):
                for i,metric in enumerate(block['MetricBlocks']):
                    key=(block['MediaSSRC'],(block['BeginSequence']+i)%65536)
                    if metric['Received'] and key not in seen and metric['ArrivalTimeOffset']<0x1ffe:
                        seen.add(key);offset_ms.append(metric['ArrivalTimeOffset']*1000/1024)
    gaps=[(stamp(b['time'])-stamp(a['time']))*1000 for a,b in zip(feedback,feedback[1:])]
    result=dict(run_id=run_id,interval_start=first['time'],interval_end=last['time'],interval_seconds=seconds,
        accepted_udp_mbps=delta('media_udp','accepted_datagram_bytes')*8/seconds/1e6,
        admitted_accounted_mbps=delta('prague','accepted_accounted_bytes')*8/seconds/1e6,
        source_submitted_mbps=delta('source','encoded_bytes_submitted')*8/seconds/1e6,
        actual_source_fps=delta('source','frames_submitted')/seconds,
        write_sample_seconds=delta('source','sample_write_ns_total')/1e9,
        pacing_wait_seconds=pacing,window_wait_seconds=window,
        residual_elapsed_seconds=seconds-pacing-window,
        pacing_wait_percent=pacing/seconds*100,window_wait_percent=window/seconds*100,
        target_mbps=describe([r['target_bps']/1e6 for r in samples]),
        window_bytes=describe([r['packet_window']*r['packet_size'] for r in after]),
        srtt_ms=describe([r['srtt_us']/1000 for r in after]),
        receiver_hold_ms_for_unique_reported_packets=describe(offset_ms),
        ccfb_dispatch_gap_ms=describe(gaps),ccfb_report_count=len(feedback),
        pre_ccfb_insufficient_full_packet_headroom_count=near_full,
        pre_ccfb_insufficient_full_packet_headroom_percent=near_full/len(feedback)*100,
        ccfb_window_release_count=releases,
        max_provisional_loss=max(r['lost'] for r in after),
        source_max_write_ms=last['source']['sample_write_ns_max']/1e6,
        source_max_tick_lateness_ms=last['source']['tick_lateness_ns_max']/1e6,
        last_sender_state=after[-1],
        qualifications=['Counter deltas share one sender interval; residual time is not a CPU utilization measurement.',
            'A wait is classified by window-blocked state at entry; simultaneous pacing deficiency is not separately attributed.',
            'Pre-feedback headroom percentage is a snapshot count at CCFB arrivals, not percent of elapsed time.',
            'Arrival-time offsets are receiver report-hold estimates for unique reports, not network delay.',
            'GCC baseline lacks these newly added sender counters; it is compared by frozen code behavior and capture metrics.'])
    return result,samples


def ideal_model(rate_bps,window_bytes,packet_bytes,rtt_ms,feedback_ms,phase_ms=0):
    """Model fixed-rate pacing and window gating with perfect timers and batched ACKs.

    Uses constant-size packets, symmetric fixed delay, periodic receiver reports,
    one-packet credit capacity, no CE/loss and an always-ready source. Results
    illustrate a mechanism; they neither replay the real core nor predict a fix.
    """
    rate=rate_bps/8; scale=1_000_000_000
    cap=packet_bytes*scale;credit=cap;at=0;inflight=0
    heap=[];ack_bytes={};accepted=0
    period=round(feedback_ms*1e6);half_rtt=round(rtt_ms*1e6/2);phase=round(phase_ms*1e6)
    warmup=1_000_000_000;end=6_000_000_000
    now=0
    while now<end:
        credit=min(cap,credit+(now-at)*rate);at=now
        while heap and heap[0]<=now:
            ack=heapq.heappop(heap);inflight-=ack_bytes.pop(ack)
        if (window_bytes is None or inflight+packet_bytes<=window_bytes) and credit>=cap:
            credit-=cap;inflight+=packet_bytes
            if now>=warmup: accepted+=packet_bytes
            arrival=now+half_rtt
            report=phase+max(0,(arrival-phase+period-1)//period)*period
            ack=report+half_rtt
            if ack not in ack_bytes: heapq.heappush(heap,ack);ack_bytes[ack]=0
            ack_bytes[ack]+=packet_bytes
        blocked=window_bytes is not None and inflight+packet_bytes>window_bytes
        deadline=heap[0] if blocked else now+max(1,math.ceil((cap-credit)/rate))
        if heap: deadline=min(deadline,heap[0])
        assert deadline>now
        now=deadline
    return accepted*8/5/1e6


def plot(metrics,samples):
    """Plot the measured allowance/output gap and the aligned sending wait budget."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    fig,(left,right)=plt.subplots(1,2,figsize=(14,5.4),gridspec_kw={'width_ratios':[1.6,1]})
    times=[r['source']['encoded_bytes_submitted']*8/r['source']['submitted_bps'] for r in samples]
    left.plot(times,[r['target_bps']/1e6 for r in samples],label='Applied Prague allowance',color='#175b9a',lw=2)
    left.plot(times,[r['media_udp']['interval_datagram_bps']/1e6 for r in samples],label='UDP media accepted per interval',color='#bc641d',lw=2)
    left.axhline(23.950988,ls='--',lw=1,color='#777777',label='Nominal encoded source: 23.95 Mbps')
    left.set(xlabel='Seconds since media loop started',ylabel='Mbps; differing byte bases labeled',title='Controller permission exceeds actual output',ylim=(0,31))
    left.legend(fontsize=9);left.grid(axis='y',alpha=.18)
    values=[metrics['pacing_wait_seconds'],metrics['window_wait_seconds'],metrics['residual_elapsed_seconds']]
    right.barh(['Pacing waits','Window waits','Other elapsed time'],values,color=['#175b9a','#bc641d','#8795a3'])
    for i,value in enumerate(values): right.text(value+.2,i,f'{value:.2f} s',va='center')
    right.invert_yaxis();right.set(xlabel='Seconds in the same 28.22-second snapshot interval',title='Measured sender waiting',xlim=(0,max(values)*1.3))
    right.grid(axis='x',alpha=.18)
    fig.suptitle('Why the new Pion Prague sender delivers less than its target',fontsize=18,x=.055,ha='left',y=.98)
    fig.text(.055,.055,'Fresh repeat on 15 Nov 2026 · fixed VP8 source · Linux timerfd · interval counter deltas; residual is not CPU utilization',fontsize=9)
    fig.text(.055,.015,'UDP acceptance is not NIC departure. Window/feedback causality remains provisional; no new physical run or configuration change.',fontsize=9,color='#555555')
    fig.tight_layout(rect=(.025,.10,.995,.92),w_pad=3)
    for extension in ('png','svg'): fig.savefig(PUBLIC/('pacing-throughput-rca-20261115.'+extension),dpi=170)
    plt.close(fig)


def main():
    """Build a reproducible numerical RCA from the frozen repeat and prior cohorts."""
    run='new-pion-v2-repeat-25m-20261115T193712Z'
    result,samples=sender_evidence(run)
    gcc_feedback=[]
    for path in sorted((ROOT/'results/raw').glob('pion-gcc-baseline-25m-20261115T181607Z-r*/sender/recording/session-trace.jsonl')):
        events=[row for row in read_jsonl(path) if row.get('event')=='controller_feedback'
                and any('CCFeedbackReport' in name for name in row['data']['packet_types'])]
        times=[stamp(row['time']) for row in events]
        gcc_feedback.append(dict(run_id=path.parents[2].name,ccfb_reports=len(events),
            dispatch_gap_ms=describe([(b-a)*1000 for a,b in zip(times,times[1:])])))
    result['gcc_feedback_observations']=gcc_feedback
    parameters=dict(rate_bps=25_000_000,window_bytes=72_000,packet_bytes=1244,rtt_ms=22)
    model=[]
    for period in (1,5,10,20):
        values=[ideal_model(**parameters,feedback_ms=period,phase_ms=period*k/4) for k in range(4)]
        model.append(dict(feedback_interval_ms=period,mean_accounted_mbps=statistics.mean(values),
                          minimum=min(values),maximum=max(values),phase_samples=values))
    result['illustrative_model']=dict(parameters=parameters,cases=model,
        without_window_gate_at_20ms_mbps=ideal_model(**dict(parameters,window_bytes=None),feedback_ms=20),
        scope='Idealized fixed-rate mechanism model, not a physical measurement, native Prague simulation or predicted improvement')
    assert result['residual_elapsed_seconds']>=0
    assert result['max_provisional_loss']==0
    assert model[0]['mean_accounted_mbps']>model[-1]['mean_accounted_mbps']
    assert abs(result['illustrative_model']['without_window_gate_at_20ms_mbps']-25)<.01
    assert all(row['dispatch_gap_ms']['median']>90 for row in gcc_feedback)
    result['code_hashes']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (
        ROOT/'internal/prague/controller.go',ROOT/'internal/prague/pacing_budget.go',ROOT/'cmd/pion-l4s/feedback.go',ROOT/'cmd/pion-l4s/main.go',
        Path('/Users/albisht/go/pkg/mod/github.com/altanai/interceptor@v0.1.51/pkg/gcc/leaky_bucket_pacer.go'),
        Path('/Users/albisht/go/pkg/mod/github.com/altanai/interceptor@v0.1.51/pkg/ccfb/interceptor.go'),Path(__file__))}
    (PUBLIC/'pacing-throughput-rca-20261115-metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    plot(result,samples)
    print(json.dumps({k:v for k,v in result.items() if k not in ('last_sender_state','code_hashes')},indent=2))


if __name__=='__main__':
    main()
