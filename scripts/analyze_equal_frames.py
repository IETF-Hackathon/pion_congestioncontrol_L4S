#!/usr/bin/env python3
"""Analyze retained two-tap equal-frame trials without fetching data or running media.

RTP identity, timestamp and marker remain visible in SRTP. Captured original
Ethernet lengths provide frame-throughput accounting, not application goodput.
The PCAPNG reader is retained from the earlier paired-capture analysis; payload
may be snaplen-truncated, so frame identity/size is independently verified from
source bytes and sender metadata rather than attempting to decode encrypted RTP.
"""
import argparse
from collections import Counter, defaultdict
import csv
import datetime
import hashlib
import json
from pathlib import Path
import re
import socket
import statistics
import struct
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def packet_rows(path, source, destination, sport, dport, payload_types):
    """Read Ethernet PCAPNG EPBs, retaining only this negotiated VP8 RTP flow."""
    blob = path.read_bytes()
    offset = 0
    endian = '<'
    interfaces = []
    rows = []
    while offset + 12 <= len(blob):
        if blob[offset:offset+4] == b'\x0a\x0d\x0d\x0a':
            endian = '<' if blob[offset+8:offset+12] == b'\x4d\x3c\x2b\x1a' else '>'
            interfaces = []
        kind, size = struct.unpack_from(endian + 'II', blob, offset)
        if size < 12 or offset + size > len(blob) or struct.unpack_from(endian+'I',blob,offset+size-4)[0] != size:
            raise RuntimeError('Invalid PCAPNG block')
        body = blob[offset+8:offset+size-4]
        if kind == 1:
            linktype = struct.unpack_from(endian+'H',body)[0]
            resolution = 1e-6
            clock_offset = 0
            cursor = 8
            while cursor + 4 <= len(body):
                code, length = struct.unpack_from(endian+'HH',body,cursor)
                cursor += 4
                value = body[cursor:cursor+length]
                if code == 9 and value:
                    power = value[0]
                    resolution = 2.0 ** -(power & 127) if power & 128 else 10.0 ** -power
                if code == 14 and len(value) == 8:
                    clock_offset = struct.unpack(endian+'q',value)[0]
                cursor += (length+3)//4*4
                if code == 0:
                    break
            interfaces.append((linktype,resolution,clock_offset))
        elif kind == 6:
            interface, high, low, captured, original = struct.unpack_from(endian+'IIIII',body)
            linktype, resolution, clock_offset = interfaces[interface]
            frame = body[20:20+captured]
            if linktype != 1:
                raise RuntimeError('Unexpected capture link type: '+str(linktype))
            if len(frame) < 34:
                offset += size; continue
            network = 14
            protocol = int.from_bytes(frame[12:14],'big')
            while protocol in (0x8100,0x88a8):
                protocol = int.from_bytes(frame[network+2:network+4],'big'); network += 4
            if protocol != 0x0800 or len(frame)<network+20 or frame[network+9]!=17:
                offset += size; continue
            if socket.inet_ntoa(frame[network+12:network+16]) != source or socket.inet_ntoa(frame[network+16:network+20]) != destination:
                offset += size; continue
            ihl = (frame[network]&15)*4
            udp = network+ihl
            if len(frame)<udp+20:
                offset += size; continue
            source_port,destination_port = struct.unpack_from('!HH',frame,udp)
            rtp = udp+8
            if ((source_port,destination_port)!=(sport,dport) or frame[rtp]>>6!=2
                    or frame[rtp+1]&127 not in payload_types):
                offset += size; continue
            sequence, timestamp, ssrc = struct.unpack_from('!HII',frame,rtp+2)
            rows.append({'time':((high<<32)|low)*resolution+clock_offset,
                         'frame_bytes':original,'ecn':frame[network+1]&3,
                         'sequence':sequence,'rtp_timestamp':timestamp,'ssrc':ssrc,'marker':bool(frame[rtp+1]&128)})
        offset += size
    if not rows:
        raise RuntimeError('No negotiated VP8 media found in '+str(path))
    return rows


def records(path):
    """Read structured endpoint or recording events, retaining original files privately."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]


def identity(packet):
    """Match one RTP packet across taps despite 16-bit sequence-number wrap."""
    return packet['ssrc'],packet['sequence'],packet['rtp_timestamp']


def percentile(values, fraction):
    """Use linear interpolation of ordered samples, matching earlier NumPy quantiles."""
    ordered=sorted(values)
    if not ordered:
        raise ValueError('no paired transit samples')
    index=(len(ordered)-1)*fraction
    lower=int(index);upper=min(lower+1,len(ordered)-1)
    return ordered[lower]+(ordered[upper]-ordered[lower])*(index-lower)


def analyze_run(item, source):
    """Correlate the selected ICE media, ECN and sender state in one completed capture."""
    folder=ROOT/'results/raw'/item['run_id'];monitor=folder/'monitor'
    manifest=json.loads((monitor/'manifest.json').read_text())
    if manifest['status']!='captured' or manifest['capture_start_attempts']!=1 or manifest['capture_partial']:
        raise ValueError('capture incomplete or ambiguous: '+item['run_id'])
    logs={role:records(folder/role/'endpoint.jsonl') for role in ('sender','receiver')}
    metadata={role:json.loads((folder/role/'metadata.json').read_text()) for role in logs}
    if metadata['sender']['video_sha256']!=source['sha256'] or metadata['sender']['exit_code']!=0:
        raise ValueError('sender source mismatch or failed sender')
    pair=manifest['selected_ice_pair']
    sender_ip,sender_port=re.search(r'host ([0-9.]+):([0-9]+)',pair['local']).groups()
    receiver_ip,receiver_port=re.search(r'host ([0-9.]+):([0-9]+)',pair['remote']).groups()
    payload_types=set()
    for path in (folder/'sender/recording').glob('*.sdp'):
        if '.raw.' not in path.name:
            payload_types.update(int(pt) for pt in re.findall(r'a=rtpmap:(\d+) VP8/90000',path.read_text(),re.I))
    if not payload_types:
        raise ValueError('negotiated VP8 payload type missing')
    arguments=(sender_ip,receiver_ip,int(sender_port),int(receiver_port),payload_types)
    ingress=packet_rows(monitor/'capture_nsi.pcapng',*arguments)
    egress=packet_rows(monitor/'capture_cmci.pcapng',*arguments)
    left={identity(row):row for row in ingress};right={identity(row):row for row in egress}
    matched=left.keys()&right.keys();missing=left.keys()-right.keys()
    transit=[(right[key]['time']-left[key]['time'])*1000 for key in matched]
    origin=min(row['time'] for row in ingress);end=max(row['time'] for row in egress)
    steady=[row for row in egress if 2<=row['time']-origin<27]
    trace=records(folder/'sender/recording/session-trace.jsonl')
    feedback=[entry['data'] for entry in trace if entry.get('event')=='controller_feedback']
    states=[entry['state_after_dispatch'] for entry in feedback]
    if not states or any(state['pacing_backend']!='linux-timerfd' for state in states):
        raise ValueError('new Linux pacer state missing or mixed')
    samples=[entry for entry in logs['sender'] if entry.get('msg')=='sender metrics']
    first,last=samples[0],samples[-1]
    elapsed=(datetime.datetime.fromisoformat(last['time'])-datetime.datetime.fromisoformat(first['time'])).total_seconds()
    frames=defaultdict(list)
    for row in left.values():
        frames[(row['ssrc'],row['rtp_timestamp'])].append(row)
    complete=[rows for rows in frames.values() if sum(row['marker'] for row in rows)==1
              and all((b['sequence']-a['sequence'])%65536==1 for a,b in
                      zip(sorted(rows,key=lambda row:row['time']),sorted(rows,key=lambda row:row['time'])[1:]))]
    ce_steps=[entry for entry in feedback if entry['state_after_dispatch']['ce']>entry['state_before_dispatch'].get('ce',0)]
    ce_downsteps=sum(entry['state_after_dispatch']['rate_bps']<entry['state_before_dispatch']['rate_bps'] for entry in ce_steps)
    flow=f'UDP [{sender_ip} {sender_port}] to [{receiver_ip} {receiver_port}]'
    with (monitor/'summary_table_downstream.csv').open() as stream:
        summary=next(row for row in csv.DictReader(stream) if row['Flow ID']==flow)
    diagnostics=(monitor/'log.txt').read_text()
    intervals=[]
    for second in range(30):
        intervals.append(sum(row['frame_bytes'] for row in egress if second<=row['time']-origin<second+1)*8/1e6)
    row=dict(run_id=item['run_id'],repetition=item['repetition'],controller='prague',
        source=source,selected_ice_pair=pair,dashboard_result_url=manifest['result_url'],flow_plot_url=manifest['flow_plot_url'],
        sender_started_at_utc=metadata['sender']['started_at'],
        sender_started_at_local=datetime.datetime.fromisoformat(metadata['sender']['started_at']).astimezone(ZoneInfo('America/Los_Angeles')).isoformat(),
        capture_partial=False,capture_start_attempts=1,
        monitor_udp_frame_mbps=float(summary['Mean data rate (Mbps)']),
        delivered_vp8_frame_mbps=sum(packet['frame_bytes'] for packet in egress)*8/(end-origin)/1e6,
        steady_delivered_vp8_frame_mbps=sum(packet['frame_bytes'] for packet in steady)*8/25/1e6,
        transit_p50_ms=percentile(transit,.5),transit_p99_ms=percentile(transit,.99),
        missing_unique_rtp_packets=len(missing),missing_media_percent=len(missing)/len(left)*100,
        ingress_rtp_packets=len(ingress),egress_rtp_packets=len(egress),unpaired_egress_rtp_packets=len(right.keys()-left.keys()),
        ingress_duplicates=len(ingress)-len(left),egress_duplicates=len(egress)-len(right),
        ingress_media_ecn_counts=dict(Counter(str(p['ecn']) for p in ingress)),
        egress_media_ecn_counts=dict(Counter(str(p['ecn']) for p in egress)),
        new_ce_between_vantages=sum(left[k]['ecn']==1 and right[k]['ecn']==3 for k in matched),
        sender_processed_ce_last_feedback=states[-1]['ce'],sender_provisional_loss_last_feedback=states[-1]['lost'],
        sender_provisional_loss_max=max(state['lost'] for state in states),
        ce_increasing_feedback_events=len(ce_steps),ce_increasing_events_with_rate_decrease=ce_downsteps,
        ce_response_limit='CE and ACK/RTT/probing/loss are processed together; coincidence is not causal isolation',
        sender_last_feedback_state=states[-1],feedback_dispatch_count=len(feedback),
        feedback_trace_truncated=any(entry.get('event')=='trace_truncated' for entry in trace),
        sender_aligned=dict(elapsed_seconds=elapsed,
            udp_accepted_mbps=(last['media_udp']['accepted_datagram_bytes']-first['media_udp']['accepted_datagram_bytes'])*8/elapsed/1e6,
            applied_target_median_mbps=statistics.median(entry['target_bps'] for entry in samples)/1e6,
            source_frames_per_second=(last['source']['frames_submitted']-first['source']['frames_submitted'])/elapsed,
            pacing_wait_seconds=(last['prague']['pacing_wait_ns']-first['prague']['pacing_wait_ns'])/1e9,
            window_wait_seconds=(last['prague']['window_wait_ns']-first['prague']['window_wait_ns'])/1e9),
        complete_ingress_frame_groups=len(complete),
        ingress_packets_per_complete_frame=dict(Counter(str(len(packets)) for packets in complete)),
        ingress_wire_bytes_per_complete_frame=dict(Counter(str(sum(p['frame_bytes'] for p in packets)) for packets in complete)),
        ingress_frame_group_limit='Complete means contiguous RTP sequences with a marker; snaplen can truncate encrypted payload',
        delivered_frame_mbps_per_second=intervals,
        process_outcomes={role:{key:metadata[role].get(key) for key in ('status','exit_code','binary_sha256','video_sha256')} for role in logs},
        receiver_errors=[entry for entry in logs['receiver'] if entry.get('level')=='ERROR'],
        capture_accounting=[line for line in diagnostics.splitlines() if 'Packets received/dropped on interface' in line],
        monitor_downstream_summary=summary,
        raw_artifacts={str(path.relative_to(ROOT)):dict(bytes=path.stat().st_size,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            for path in monitor.iterdir() if path.suffix in ('.pcapng','.csv')})
    (folder/'analysis.json').write_text(json.dumps(row,indent=2)+'\n')
    return row


def report(batch):
    """Write public aggregate JSON/CSV/Markdown after both independent captures finish."""
    base=ROOT/batch;plan=json.loads((base/'plan.json').read_text());progress=json.loads((base/'progress.json').read_text())
    if progress['status']!='captured' or len(progress['completed'])!=2:
        raise ValueError('both requested captures must finish before analysis')
    rows=[analyze_run(item,plan['source']) for item in plan['runs']]
    fields=('steady_delivered_vp8_frame_mbps','monitor_udp_frame_mbps','transit_p50_ms','transit_p99_ms','missing_media_percent')
    stats={field:dict(mean=statistics.mean(row[field] for row in rows),minimum=min(row[field] for row in rows),
                     maximum=max(row[field] for row in rows),n=2) for field in fields}
    notes=plan['limitations']+[
        'Normal Prague retains CE, ACK/RTT, probing and provisional-loss response; this is not CE-only control',
        'Frame throughput includes headers; paired transit includes fixed delay; pure queue delay and application goodput are unavailable',
        'Final feedback state may omit a tail; provisional sender loss differs from missing RTP at the capture taps',
        'Receiver closed-pipe shutdown is retained: trial 1 exited with an error after peer close; trial 2 was interrupted for cleanup',
        'Each capture interface reported zero pcap/dumpcap drops and one flushed packet; zero missing media applies to retained packets',
        'Capture interface mapping follows the retained UI config: NSI=enp2s0, CMCI=enp3s0; qdisc validation is separate',
        'Earlier GCC and variable-frame Prague runs use a different source and are historical context, not matched baselines']
    previous=json.loads((ROOT/'results/preview/pion-prague-pacing-v2-25m-20261115T190910Z.json').read_text())
    reference_inputs=previous['plan']['runtime_source_hashes']
    differences=[path for path,digest in plan['source_sha256'].items() if reference_inputs.get(path)!=digest]
    result=dict(schema_version=1,comparison_id=plan['batch_id'],scope=plan['scope'],source=plan['source'],
        plan=plan,runs=rows,statistics=stats,limitations=notes,
        previous_new_pacer_runtime_comparison=dict(reference=previous['comparison_id'],compiled_input_differences=differences),
        analysis_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    prefix=ROOT/'results/preview'/plan['batch_id']
    prefix.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    columns=('run_id',)+fields+('new_ce_between_vantages','sender_processed_ce_last_feedback','sender_provisional_loss_last_feedback','dashboard_result_url')
    with prefix.with_suffix('.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        writer.writerows({key:row[key] for key in columns} for row in rows)
    lines=['# Equal-frame new-pacer Prague trials','',
        f"Every source frame: **{plan['source']['largest_frame_bytes']:,} bytes**, byte-identical VP8 keyframe, nominal 60 fps; offered encoded payload **{plan['source']['nominal_payload_bps']/1e6:.6f} Mbps**. Initial controller target 25 Mbps. No live encoder adaptation.",'',
        '| Trial | Steady frame Mbps | Transit P50 / P99 ms | Missing RTP | Capture CE / sender CE | Provisional sender loss |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:
        lines.append(f"| [{row['repetition']}]({row['dashboard_result_url']}) | {row['steady_delivered_vp8_frame_mbps']:.3f} | {row['transit_p50_ms']:.3f} / {row['transit_p99_ms']:.3f} | {row['missing_unique_rtp_packets']} | {row['new_ce_between_vantages']} / {row['sender_processed_ce_last_feedback']} | {row['sender_provisional_loss_last_feedback']} |")
    lines+=['',f"Mean steady frame rate: **{stats['steady_delivered_vp8_frame_mbps']['mean']:.3f} Mbps**. Mean per-trial P99 transit: **{stats['transit_p99_ms']['mean']:.3f} ms**.",'',
        f"[JSON]({prefix.name}.json) · [CSV]({prefix.name}.csv)",'','## Sender and frame-shape evidence','']
    for row in rows:
        a=row['sender_aligned']
        lines.append(f"- Trial {row['repetition']}: source {a['source_frames_per_second']:.2f} fps submitted; median applied allowance {a['applied_target_median_mbps']:.3f} Mbps; UDP acceptance {a['udp_accepted_mbps']:.3f} Mbps. Aligned pacing/window waits {a['pacing_wait_seconds']:.3f}/{a['window_wait_seconds']:.3f} s. Complete ingress frame packet counts: {row['ingress_packets_per_complete_frame']}; wire-byte totals: {row['ingress_wire_bytes_per_complete_frame']}. CE-increasing feedback batches: {row['ce_increasing_feedback_events']}; simultaneous rate decreases: {row['ce_increasing_events_with_rate_decrease']}. [Flow plot]({row['flow_plot_url']}).")
    lines+=['','## Limits','']+['- '+note for note in notes]
    prefix.with_suffix('.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(report=str(prefix.with_suffix('.md')),statistics=stats,
        runs=[{key:row[key] for key in columns} for row in rows]),indent=2))


def main():
    """Analyze one explicit completed batch using only retained local artifacts."""
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--batch',required=True)
    args=parser.parse_args();report(args.batch)


if __name__=='__main__':
    main()

