"""Retain the two capture vantages and plot this matched fixed-source comparison."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin, unquote
import csv
import datetime
import hashlib
import http.client
import json
import math
import os
import re
import socket
import struct
from zoneinfo import ZoneInfo

os.environ['MPLCONFIGDIR'] = str(Path(__file__).resolve().parent / 'mpl-cache')
os.environ['XDG_CACHE_HOME'] = str(Path(__file__).resolve().parent / 'cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path('/Users/albisht/ietf/pion-l4s-interop')
BASE = Path(__file__).resolve().parent
PLAN = json.loads((BASE / 'plan.json').read_text())
PUBLIC = ROOT / 'results/preview'
STEM = 'pion-prague-vs-gcc-25m-20261115T172333Z'

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.append(dict(attrs).get('href', ''))

def download(folder, href, destination):
    url = urlsplit(urljoin(folder, href))
    if url.hostname != 'lld-black4.local' or not url.path.startswith(urlsplit(folder).path):
        raise RuntimeError('Refusing an artifact outside this monitor result')
    if destination.is_file():
        return
    connection = http.client.HTTPConnection(url.hostname, timeout=45)
    try:
        connection.request('GET', url.path)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f'Artifact HTTP {response.status}: {destination.name}')
        data = response.read(128 * 1024 * 1024 + 1)
        if len(data) > 128 * 1024 * 1024:
            raise RuntimeError('Artifact exceeds 128 MiB bound')
        destination.write_bytes(data)
    finally:
        connection.close()

def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.startswith('{')]

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
                         'sequence':sequence,'rtp_timestamp':timestamp,'ssrc':ssrc})
        offset += size
    if not rows:
        raise RuntimeError('No negotiated VP8 media found in '+str(path))
    return rows

def instant(text):
    normalized = re.sub(r'(\.\d{6})\d+', r'\1', text.replace('Z','+00:00'))
    return datetime.datetime.fromisoformat(normalized).timestamp()

def key(row):
    return row['ssrc'],row['sequence'],row['rtp_timestamp']

def per_second(rows, origin, field='frame_bytes'):
    times=np.array([row['time']-origin for row in rows])
    values=np.array([row[field] for row in rows])
    return np.histogram(times,bins=np.arange(0,31),weights=values)[0]

def analyze(item):
    folder=ROOT/'results/raw'/item['run_id']
    monitor=folder/'monitor'
    manifest=json.loads((monitor/'manifest.json').read_text())
    url=manifest['result_url']
    parser=Links();parser.feed((monitor/'result-index.html').read_text())
    available={unquote(link).removeprefix('./'):link for link in parser.links
               if not link.startswith(('/','?')) and '..' not in unquote(link)}
    logs={role:records(folder/role/'endpoint.jsonl') for role in ('sender','receiver')}
    metadata={role:json.loads((folder/role/'metadata.json').read_text()) for role in logs}
    pair=next(row for row in logs['sender'] if row.get('msg')=='selected ICE pair')
    sender_ip,sender_port=re.search(r'host ([0-9.]+):([0-9]+)',pair['local']).groups()
    receiver_ip,receiver_port=re.search(r'host ([0-9.]+):([0-9]+)',pair['remote']).groups()
    forward=f'UDP [{sender_ip} {sender_port}] to [{receiver_ip} {receiver_port}]'
    reverse=f'UDP [{receiver_ip} {receiver_port}] to [{sender_ip} {sender_port}]'
    wanted=['log.txt','summary_table_downstream.csv','summary_table_upstream.csv',
            'Aggregate_thruput_downstream.pdf','Aggregate_thruput_upstream.pdf',
            'capture_nsi.pcapng','capture_cmci.pcapng',forward+'latency.csv',reverse+'latency.csv']
    for name in wanted:
        if name in available:
            download(url,available[name],monitor/name)
    with (monitor/'summary_table_downstream.csv').open() as stream:
        summary=next(row for row in csv.DictReader(stream) if row['Flow ID']==forward)
    with (monitor/'summary_table_upstream.csv').open() as stream:
        upstream=next(row for row in csv.DictReader(stream) if row['Flow ID']==reverse)
    flow_pdf=summary['Flow Number']+'_downstream.pdf'
    if flow_pdf in available:
        download(url,available[flow_pdf],monitor/flow_pdf)
    types=set()
    for path in (folder/'sender/recording').glob('*.sdp'):
        if '.raw.' not in path.name:
            types.update(int(pt) for pt in re.findall(r'a=rtpmap:(\d+) VP8/90000',path.read_text(),re.I))
    if not types:
        raise RuntimeError('VP8 payload type missing from retained redacted SDP')
    kwargs=(sender_ip,receiver_ip,int(sender_port),int(receiver_port),types)
    ingress=packet_rows(monitor/'capture_nsi.pcapng',*kwargs)
    egress=packet_rows(monitor/'capture_cmci.pcapng',*kwargs)
    left={key(row):row for row in ingress};right={key(row):row for row in egress}
    matched=left.keys()&right.keys()
    missing=left.keys()-right.keys()
    extra=right.keys()-left.keys()
    origin=min(row['time'] for row in ingress)
    end=max(row['time'] for row in egress)
    duration=end-origin
    delays=np.array([(right[k]['time']-left[k]['time'])*1000 for k in matched])
    marked=[row for row in egress if row['ecn']==3]
    new_ce=[k for k in matched if left[k]['ecn']==1 and right[k]['ecn']==3]
    ce_counts={str(code):sum(row['ecn']==code for row in egress) for code in range(4)}
    source_counts={str(code):sum(row['ecn']==code for row in ingress) for code in range(4)}
    steady=[row for row in egress if 2<=row['time']-origin<27]
    receiver_samples=[row for row in logs['receiver'] if row.get('msg')=='receiver metrics']
    sender_samples=[row for row in logs['sender'] if row.get('msg')=='sender metrics']
    trace=records(folder/'sender/recording/session-trace.jsonl')
    feedback=[row for row in trace if row.get('event')=='controller_feedback']
    states=[row['data']['state_after_dispatch'] for row in feedback]
    loss_states=[state.get('lost') for state in states if 'lost' in state]
    controller_last=states[-1] if states else {}
    packet_lengths=[row['frame_bytes'] for row in ingress]
    gaps=np.diff([row['time'] for row in ingress if row['frame_bytes']>=1100])*1e6
    output={
        'controller':item['controller'],'run_id':item['run_id'],'source_mbps':PLAN['source']['nominal_payload_bps']/1e6,
        'initial_controller_target_mbps':25,'dashboard_result_url':url,
        'flow_plot_url':url+available.get(flow_pdf,''),'selected_ice_pair':pair,
        'sender_started_at_utc':metadata['sender']['started_at'],'sender_finished_at_utc':metadata['sender']['finished_at'],
        'sender_started_at_local':datetime.datetime.fromtimestamp(instant(metadata['sender']['started_at']),ZoneInfo('America/Los_Angeles')).isoformat(),
        'capture_partial':manifest['capture_partial'],'capture_start_attempts':manifest['capture_start_attempts'],
        'media_interval_seconds':duration,'media_first_at_utc':datetime.datetime.fromtimestamp(origin,datetime.timezone.utc).isoformat(),
        'monitor_udp_frame_mbps':float(summary['Mean data rate (Mbps)']),
        'delivered_vp8_frame_mbps':sum(row['frame_bytes'] for row in egress)*8/duration/1e6,
        'steady_delivered_vp8_frame_mbps':sum(row['frame_bytes'] for row in steady)*8/25/1e6,
        'ingress_rtp_packets':len(ingress),'egress_rtp_packets':len(egress),
        'matched_unique_rtp_packets':len(matched),'missing_unique_rtp_packets':len(missing),
        'missing_media_percent':len(missing)/len(left)*100,
        'ingress_duplicate_media_packets':len(ingress)-len(left),
        'egress_duplicate_media_packets':len(egress)-len(right),
        'unpaired_egress_rtp_packets':len(extra),'monitor_udp_drops':int(summary['Dropped Packets']),
        'ingress_media_ecn_counts':source_counts,'egress_media_ecn_counts':ce_counts,
        'new_ce_between_vantages':len(new_ce),'sender_processed_ce_last_feedback':controller_last.get('ce'),
        'sender_provisional_loss_last_feedback':controller_last.get('lost'),
        'sender_provisional_loss_max':max(loss_states) if loss_states else None,
        'transit_p50_ms':float(np.percentile(delays,50)),'transit_p99_ms':float(np.percentile(delays,99)),
        'large_ingress_packet_gap_p50_us':float(np.median(gaps)) if len(gaps) else None,
        'receiver_last_periodic_sample':receiver_samples[-1] if receiver_samples else None,
        'sender_last_periodic_sample':sender_samples[-1] if sender_samples else None,
        'sender_last_feedback_state':controller_last,'feedback_dispatch_count':len(feedback),
        'feedback_trace_truncated':any(row.get('event')=='trace_truncated' for row in trace),
        'process_outcomes':{role:{key:metadata[role].get(key) for key in ('status','exit_code','binary_sha256','video_sha256')} for role in logs},
        'monitor_downstream_summary':summary,'monitor_upstream_summary':upstream,
        'raw_artifacts':{str(path.relative_to(ROOT)):{'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in monitor.iterdir() if path.suffix in ('.pcapng','.csv','.pdf')},
    }
    output['controller_target_series']=[{'elapsed_seconds':instant(row['time'])-instant(sender_samples[0]['time'])+1,
                                       'target_mbps':row.get('target_bps',row.get('gcc_target_bps',0))/1e6}
                                      for row in sender_samples]
    output['delivered_frame_mbps_per_second']=(per_second(egress,origin)*8/1e6).tolist()
    output['ce_per_second']=np.histogram([row['time']-origin for row in marked],bins=np.arange(0,31))[0].tolist()
    output['drops_per_second']=np.histogram([left[k]['time']-origin for k in missing],bins=np.arange(0,31))[0].tolist()
    with (folder/'matched-media-packets.csv').open('w') as stream:
        writer=csv.writer(stream);writer.writerow(['ssrc','sequence','rtp_timestamp','ingress_epoch','egress_epoch','transit_ms','ingress_ecn','egress_ecn'])
        for k in sorted(matched,key=lambda k:left[k]['time']):
            a,b=left[k],right[k]
            writer.writerow([*k,a['time'],b['time'],(b['time']-a['time'])*1000,a['ecn'],b['ecn']])
    (folder/'analysis.json').write_text(json.dumps(output,indent=2)+'\n')
    return output,delays

def main():
    progress=json.loads((BASE/'progress.json').read_text())
    if len(progress.get('completed',[]))!=2:
        raise RuntimeError('Both captured runs must finish before analysis')
    analyzed=[analyze(item) for item in PLAN['runs']]
    results=[result for result,_ in analyzed]
    notes=[
        'One Prague run followed by one GCC run; 30 seconds each, with separate 60-second captures. No repetitions or controlled competing traffic.',
        'Same 23.950988 Mbps prerecorded VP8 clip and 25 Mbps initial target; no adaptive encoder. Actual source frame production can be slowed by pacing/backpressure.',
        'Prague uses application ECT(1)/ECN-aware CCFB; GCC uses CCFB without the application ECN socket. These are complete transport modes, including their different pacers.',
        'Capture interface mapping was read live: NSI=enp2s0, CMCI=enp3s0. Shaping capacity, active DualPI2/SRM mode and queue telemetry were not independently verified.',
        'Monitor frame throughput includes headers. VP8 frame throughput filters negotiated media payload types at one egress vantage; it is not decoded-video goodput.',
        'Paired transit includes fixed propagation/emulation delay and is not pure queue delay. Missing RTP identities across captures are reported separately from monitor UDP drops and provisional controller loss.',
        'The steady comparison window is 2 to 27 seconds after the first ingress media packet in each capture. One-second throughput bins use that same ingress media origin.',
        'Controller target series is aligned approximately using one-second sender metric samples; it is not packet-synchronized with the capture timeline.',
        'Endpoint periodic counters may omit a tail; packet counts and ECN reconciliation use both complete PCAPNG vantages. Sender CE uses the last recorded feedback dispatch, not merely the last periodic metric.',
        'Prague had both CE and packet loss in this run; its native controller responds to both, so rate changes cannot be attributed solely to CE. The roughly 9 Mbps sender pacing ceiling also remains a confounder.',
        'Both capture logs report pcap:0 and dumpcap:0 dropped packets; each interface flushed one packet at shutdown, well after the bounded media interval. The monitor lacks snmpwalk, so DOCSIS MIB telemetry was unavailable.',
    ]
    report={'schema_version':1,'comparison_id':PLAN['batch_id'],'source_commit':PLAN['source_commit'],
            'source':PLAN['source'],'scope':PLAN['scope'],'runs':results,'limitations':notes,
            'unavailable_metrics':{'pure_queue_delay_ms':None,'deduplicated_application_goodput_bps':None,'adaptive_encoder_target_bps':None,'raw_rtt_variation_ms':None},
            'requirement_status':{'live_qdisc_settings':'missing','nominal_vs_actual_rate':'partial','congestion_response':'partial','adaptive_source':'missing','matched_physical_transport_baseline':'verified for one run per controller','six_metrics_and_repetitions':'partial'}}
    PUBLIC.mkdir(exist_ok=True)
    (PUBLIC/(STEM+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    fields=['controller','source_mbps','monitor_udp_frame_mbps','delivered_vp8_frame_mbps','steady_delivered_vp8_frame_mbps','ingress_rtp_packets','egress_rtp_packets','new_ce_between_vantages','sender_processed_ce_last_feedback','missing_unique_rtp_packets','monitor_udp_drops','transit_p50_ms','transit_p99_ms','dashboard_result_url']
    with (PUBLIC/(STEM+'.csv')).open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        writer.writerows({key:row.get(key) for key in fields} for row in results)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    figure,axes=plt.subplots(2,2,figsize=(15,9.2))
    colors={'prague':'#166b9d','gcc':'#c35b16'}
    for result,delays in analyzed:
        mode=result['controller'];color=colors[mode];label=mode.upper() if mode=='gcc' else 'Prague'
        axes[0,0].plot(np.arange(30)+.5,result['delivered_frame_mbps_per_second'],label=label,color=color,lw=2)
        series=result['controller_target_series']
        axes[0,1].plot([row['elapsed_seconds'] for row in series],[row['target_mbps'] for row in series],label=label,color=color,lw=2)
        ordered=np.sort(delays)
        indices=np.unique(np.linspace(0,len(ordered)-1,min(1500,len(ordered))).astype(int))
        axes[1,0].plot(ordered[indices],(indices+1)/len(ordered)*100,label=label,color=color,lw=2)
        axes[1,1].step(np.arange(31),np.r_[0,np.cumsum(result['ce_per_second'])],where='post',label=label,color=color,lw=2)
    for ax in axes[0]:
        ax.axhline(PLAN['source']['nominal_payload_bps']/1e6,color='#555555',ls='--',lw=1,label='Encoded source: 23.95 Mbps')
        ax.set_xlim(0,30);ax.set_xlabel('Seconds into each run');ax.set_ylabel('Mbps');ax.legend(fontsize=9)
    axes[0,0].set_title('Delivered VP8 media at client-side capture')
    axes[0,1].set_title('Requested controller pacing target')
    axes[1,0].set_title('Transit across the two monitor interfaces')
    axes[1,0].set_xlabel('One-way transit (ms; includes fixed delay)')
    axes[1,0].set_ylabel('Matched media packets (%)');axes[1,0].set_ylim(0,100)
    axes[1,0].legend(fontsize=9)
    axes[1,1].set_title('CE-marked media observed at client side')
    axes[1,1].set_xlabel('Seconds into each run');axes[1,1].set_ylabel('Cumulative CE packets');axes[1,1].set_xlim(0,30);axes[1,1].legend(fontsize=9)
    for ax in axes.flat:
        ax.grid(alpha=.18)
    lines=[]
    for row in results:
        label='Prague' if row['controller']=='prague' else 'GCC'
        lines.append(f"{label}: {row['delivered_vp8_frame_mbps']:.2f} Mbps media frames | CE {row['egress_media_ecn_counts']['3']} | missing across captures {row['missing_unique_rtp_packets']} | transit P99 {row['transit_p99_ms']:.2f} ms")
    figure.suptitle('Same ~25 Mbps video source: Prague then GCC',fontsize=19,x=.06,ha='left',y=.985)
    figure.text(.06,.935,'15 Nov 2026 · 30 seconds per controller · CCFB feedback · Linux .216 → lld-black4 → Mac .79',fontsize=11,color='#444444')
    figure.text(.06,.065,'\n'.join(lines),fontsize=10)
    figure.text(.06,.018,'Fixed source; no adaptive encoder. GCC media is Non-ECT. Active qdisc/capacity unverified; background traffic uncontrolled. One run each.',fontsize=9,color='#555555')
    figure.tight_layout(rect=(.035,.115,.995,.92),h_pad=2.2,w_pad=2)
    figure.savefig(PUBLIC/(STEM+'.png'),dpi=170)
    figure.savefig(PUBLIC/(STEM+'.svg'))
    plt.close(figure)
    lines=['# Prague versus GCC: approximately 25 Mbps source','',
           'One physical transport run per controller, Prague first, on 15 November 2026.','',
           f"Source: {PLAN['source']['nominal_payload_bps']/1e6:.6f} Mbps VP8, 1280×720 at 60 fps; identical SHA-256 `{PLAN['source']['sha256']}`.",'',
           '| Controller | Monitor UDP Mbps | VP8 frame Mbps | Steady VP8 Mbps | CE at egress / sender | Missing RTP across captures | Transit P50 / P99 ms |',
           '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for row in results:
        sender_ce=row['sender_processed_ce_last_feedback'] if row['sender_processed_ce_last_feedback'] is not None else 'n/a'
        lines.append(f"| {row['controller']} | {row['monitor_udp_frame_mbps']:.3f} | {row['delivered_vp8_frame_mbps']:.3f} | {row['steady_delivered_vp8_frame_mbps']:.3f} | {row['egress_media_ecn_counts']['3']} / {sender_ce} | {row['missing_unique_rtp_packets']} | {row['transit_p50_ms']:.3f} / {row['transit_p99_ms']:.3f} |")
    lines+=['','GCC sender CE is unavailable because that mode does not use the application ECN feedback adapter. Capture CE=0 is a measured value; sender CE=null is unavailable.','',f'![Comparison]({STEM}.png)','']
    lines+=['## Observations','',
            f"GCC delivered more media frames ({results[1]['delivered_vp8_frame_mbps']:.2f} versus {results[0]['delivered_vp8_frame_mbps']:.2f} Mbps), with a larger missing-media fraction ({results[1]['missing_media_percent']:.2f}% versus {results[0]['missing_media_percent']:.3f}%) and higher transit delay in these captures.",'',
            'All 324 Prague CE marks were newly observed between the two capture sides and reached the recorded sender controller state. GCC media was entirely Non-ECT, so its zero CE count does not imply absence of congestion.','',
            'Prague also observed 13 losses. Its rate response includes CE and loss handling; this was not a CE-only experiment. Its lower delivered rate also makes the latency difference unsuitable as an isolated proof of controller superiority.','',
            f'[Summary CSV]({STEM}.csv) · [Full measurements and provenance]({STEM}.json)','']
    for row in results:
        lines += [f"- [{row['controller']} monitor results]({row['dashboard_result_url']}), started {row['sender_started_at_local']}."]
    lines+=['','## Interpretation limits','']+['- '+note for note in notes]
    lines+=['','Pure queue delay, deduplicated application goodput, raw RTT variation and an adaptive encoder target remain unavailable. These two runs do not complete the full performance-validation matrix.','']
    (PUBLIC/(STEM+'.md')).write_text('\n'.join(lines))
    print(json.dumps({'plot':str(PUBLIC/(STEM+'.png')),'results':[{key:row.get(key) for key in fields} for row in results]},indent=2))

if __name__=='__main__':
    main()
