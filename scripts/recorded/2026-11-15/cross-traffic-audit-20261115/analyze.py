"""Measure non-Pion traffic overlapping the retained physical test captures."""
from pathlib import Path
from collections import Counter, defaultdict, deque, namedtuple
import bisect
import csv
import datetime
import hashlib
import json
import os
import re
import socket
import struct

BASE=Path(__file__).resolve().parent
ROOT=Path('/Users/albisht/ietf/pion-l4s-interop')
os.environ['MPLCONFIGDIR']=str(BASE/'mpl-cache')
os.environ['XDG_CACHE_HOME']=str(BASE/'cache')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

Packet=namedtuple('Packet','time length source_mac destination_mac source destination protocol sport dport media_pt fingerprint')

def packets(path):
    blob=path.read_bytes();position=0;endian='<';interfaces=[];result=[]
    while position+12<=len(blob):
        if blob[position:position+4]==b'\x0a\x0d\x0d\x0a':
            endian='<' if blob[position+8:position+12]==b'\x4d\x3c\x2b\x1a' else '>'
            interfaces=[]
        kind,size=struct.unpack_from(endian+'II',blob,position)
        if size<12 or position+size>len(blob):raise RuntimeError('Invalid PCAPNG block')
        body=blob[position+8:position+size-4];position+=size
        if kind==1:
            linktype=struct.unpack_from(endian+'H',body)[0];resolution=1e-6;offset=0;cursor=8
            while cursor+4<=len(body):
                code,length=struct.unpack_from(endian+'HH',body,cursor);cursor+=4
                value=body[cursor:cursor+length]
                if code==9 and value:
                    exponent=value[0];resolution=2.0**-(exponent&127) if exponent&128 else 10.0**-exponent
                if code==14 and len(value)==8:offset=struct.unpack(endian+'q',value)[0]
                cursor+=(length+3)//4*4
                if code==0:break
            interfaces.append((linktype,resolution,offset))
        if kind!=6:continue
        interface,high,low,captured,original=struct.unpack_from(endian+'IIIII',body)
        linktype,resolution,offset=interfaces[interface]
        if linktype!=1:raise RuntimeError('Non-Ethernet capture')
        frame=body[20:20+captured]
        if len(frame)<34:continue
        ether=int.from_bytes(frame[12:14],'big');network=14
        while ether in (0x8100,0x88a8):
            ether=int.from_bytes(frame[network+2:network+4],'big');network+=4
        layer=frame[network:]
        if ether==0x0800 and len(layer)>=20:
            protocol=layer[9];transport=(layer[0]&15)*4
            source=socket.inet_ntop(socket.AF_INET,layer[12:16]);destination=socket.inet_ntop(socket.AF_INET,layer[16:20])
            total=int.from_bytes(layer[2:4],'big');normalized=bytearray(layer[:64]);normalized[1]=0;normalized[10:12]=b'\0\0'
        elif ether==0x86dd and len(layer)>=40:
            protocol=layer[6];transport=40
            source=socket.inet_ntop(socket.AF_INET6,layer[8:24]);destination=socket.inet_ntop(socket.AF_INET6,layer[24:40])
            total=int.from_bytes(layer[4:6],'big')+40;normalized=bytearray(layer[:64]);normalized[0]&=240;normalized[1]&=15
            while protocol in (0,43,44,60,51) and len(layer)>=transport+2:
                old=protocol;protocol=layer[transport]
                length=8 if old==44 else ((layer[transport+1]+2)*4 if old==51 else (layer[transport+1]+1)*8)
                transport+=length
        else:continue
        sport=dport=0;media_pt=None
        if protocol in (6,17) and len(layer)>=transport+4:
            sport,dport=struct.unpack_from('!HH',layer,transport)
            checksum=transport+(16 if protocol==6 else 6)
            if checksum+2<=len(normalized):normalized[checksum:checksum+2]=b'\0\0'
            if protocol==17 and len(layer)>=transport+10 and layer[transport+8]>>6==2:
                media_pt=layer[transport+9]&127
        fingerprint=hashlib.blake2s(bytes(normalized)+struct.pack('!I',total),digest_size=12).digest()
        result.append(Packet(((high<<32)|low)*resolution+offset,original,frame[6:12].hex(':'),frame[:6].hex(':'),source,destination,protocol,sport,dport,media_pt,fingerprint))
    return result

def identity(packet):
    return (packet.protocol,packet.source,packet.sport,packet.destination,packet.dport)

def flow_label(flow):
    protocol,source,sport,destination,dport=flow
    return f"{ {6:'TCP',17:'UDP',1:'ICMP',58:'ICMPv6'}.get(protocol,str(protocol))} {source}:{sport} -> {destination}:{dport}"

def analyze(run_id,label,trial):
    folder=ROOT/'results/raw'/run_id
    analysis=json.loads((folder/'analysis.json').read_text())
    pair=analysis['selected_ice_pair']
    sender,sport=re.search(r'host ([0-9.]+):([0-9]+)',pair['local']).groups()
    receiver,rport=re.search(r'host ([0-9.]+):([0-9]+)',pair['remote']).groups()
    own=(17,sender,int(sport),receiver,int(rport));reverse=(17,receiver,int(rport),sender,int(sport))
    def category(packet):
        flow=identity(packet)
        if flow==own and packet.media_pt==96:return 'Pion media'
        if flow in (own,reverse):return 'Experiment control'
        if packet.protocol==6 and {packet.source,packet.destination}=={sender,receiver} and (packet.sport in (22,8080) or packet.dport in (22,8080)):
            return 'Experiment control'
        if '192.168.1.75' in (packet.source,packet.destination) and packet.protocol==6 and (packet.sport in (80,443) or packet.dport in (80,443)):
            return 'Monitor/UI traffic'
        return 'Other background'
    ingress=packets(folder/'monitor/capture_nsi.pcapng')
    egress=packets(folder/'monitor/capture_cmci.pcapng')
    media=[packet for packet in egress if category(packet)=='Pion media']
    assert len(media)==analysis['egress_rtp_packets'],(run_id,'media parser count',len(media),analysis['egress_rtp_packets'])
    macs={packet.destination_mac for packet in media};assert len(macs)==1
    mac=next(iter(macs));origin=datetime.datetime.fromisoformat(analysis['media_first_at_utc'].replace('Z','+00:00')).timestamp()
    start,end=origin+2,origin+27
    left=[p for p in ingress if p.destination_mac==mac and start<=p.time<end]
    right=[p for p in egress if p.destination_mac==mac and start<=p.time<end]
    upstream=[p for p in egress if p.source_mac==mac and start<=p.time<end]
    classes=('Pion media','Other background','Monitor/UI traffic','Experiment control')
    def metrics(rows):
        output={}
        for name in classes:
            chosen=[p for p in rows if category(p)==name]
            bins=np.histogram([p.time-origin for p in chosen],bins=np.arange(0,31),weights=[p.length for p in chosen])[0]*8/1e6
            output[name]={'packets':len(chosen),'mean_mbps':sum(p.length for p in chosen)*8/25/1e6,'peak_1s_mbps':float(max(bins[2:27])),'mbps_per_second':bins.tolist()}
        return output
    observed=defaultdict(deque)
    for p in sorted(egress,key=lambda p:p.time):
        if p.destination_mac==mac and start-.002<=p.time<=end+1 and category(p)=='Other background':observed[p.fingerprint].append(p.time)
    paired=0;paired_flows=set();delays=[]
    background=[p for p in left if category(p)=='Other background']
    for p in sorted(background,key=lambda p:p.time):
        candidates=observed[p.fingerprint]
        while candidates and candidates[0]<p.time-.002:candidates.popleft()
        if candidates and candidates[0]<=p.time+1:
            paired+=1;paired_flows.add(identity(p));delays.append((candidates.popleft()-p.time)*1000)
    flow_bytes=Counter();flow_packets=Counter()
    for p in background:flow_bytes[identity(p)]+=p.length;flow_packets[identity(p)]+=1
    top=[{'flow':flow_label(flow),'mean_mbps':size*8/25/1e6,'packets':flow_packets[flow],'seen_at_both_interfaces':flow in paired_flows} for flow,size in flow_bytes.most_common(5)]
    result={'run_id':run_id,'condition':label,'trial':trial,'result_url':analysis['dashboard_result_url'],
            'window_start_utc':datetime.datetime.fromtimestamp(start,datetime.timezone.utc).isoformat(),
            'window_end_utc':datetime.datetime.fromtimestamp(end,datetime.timezone.utc).isoformat(),
            'window_seconds':25,'receiver_mac_inferred_from_pion':mac,
            'server_side_downstream':metrics(left),'client_side_downstream':metrics(right),
            'client_side_upstream':metrics(upstream),
            'background_flow_count':len(flow_bytes),'background_packet_observations_matched_between_interfaces':paired,
            'background_flows_seen_at_both_interfaces':len(paired_flows),
            'paired_background_transit_p50_ms':float(np.median(delays)) if delays else None,
            'largest_background_flows':top,'video_media_count_matches_saved_analysis':True,
            'capture_sha256':{name:hashlib.sha256((folder/'monitor'/name).read_bytes()).hexdigest() for name in ('capture_nsi.pcapng','capture_cmci.pcapng')}}
    print(json.dumps({'condition':label,'trial':trial,'background_in_mbps':result['server_side_downstream']['Other background']['mean_mbps'],'background_out_mbps':result['client_side_downstream']['Other background']['mean_mbps'],'background_peak_mbps':result['server_side_downstream']['Other background']['peak_1s_mbps'],'monitor_in_mbps':result['server_side_downstream']['Monitor/UI traffic']['mean_mbps'],'paired_background_packets':paired,'top':top[:2]}),flush=True)
    return result

def main():
    selected=[]
    for prefix,label in [('pion-gcc-baseline-25m-20261115T181607Z','GCC baseline'),('pion-prague-pacing-v2-25m-20261115T190910Z','Prague pacing v2')]:
        for trial in range(1,4):selected.append((prefix+'-r'+str(trial),label,trial))
    selected.append(('new-pion-v2-repeat-25m-20261115T193712Z','Latest Prague repeat',1))
    rows=[analyze(*item) for item in selected]
    report={'scope':'Read-only audit of other traffic overlapping seven retained physical media captures',
            'definition':'Common 25-second interval, seconds 2–27 after first ingress media, separately for each run. Rates use captured original frame lengths and one vantage at a time, not sums of unequal per-flow averages.',
            'classification':{'Pion media':'Selected VP8 RTP payload type 96 on negotiated sender-to-receiver UDP pair',
                              'Experiment control':'Other traffic on selected UDP pair, plus TCP signaling 8080 and SSH 22 between the peers',
                              'Monitor/UI traffic':'TCP 80/443 to or from bottleneck management IP 192.168.1.75',
                              'Other background':'Remaining IPv4/IPv6 unicast traffic to or from the receiver Ethernet MAC; not asserted to belong to a particular application or person'},
            'runs':rows,'limitations':['NSI=enp2s0 and CMCI=enp3s0 are the recorded capture-vantage mapping. Live AQM mode/capacity are not independently established.',
                                      'Client-bound background shares the monitored forwarding path; this does not prove a particular queue classification or causal contribution to each CE/loss event.',
                                      'Unknown encrypted application traffic is classified by transport/IP/ports, not by guessed website or app. Extra flows are not proof of another participant or of deliberate controlled competing traffic.',
                                      'Broadcast, multicast and non-IP frames are excluded. Metrics are therefore a conservative unicast/IP subset of competing traffic.',
                                      'Background packet correspondence uses normalized captured IP headers/prefixes and a bounded timestamp window; counts are evidence of common forwarding, not definitive background-loss accounting.']}
    public=ROOT/'results/preview';stem='cross-traffic-overlap-20261115'
    (public/(stem+'.json')).write_text(json.dumps(report,indent=2)+'\n')
    with (public/(stem+'.csv')).open('w') as stream:
        writer=csv.writer(stream);writer.writerow(['condition','trial','background_ingress_mbps','background_delivered_mbps','background_ingress_peak_1s_mbps','monitor_ui_ingress_mbps','experiment_control_ingress_mbps','background_packets_matched_both_sides','result_url'])
        for row in rows:writer.writerow([row['condition'],row['trial'],row['server_side_downstream']['Other background']['mean_mbps'],row['client_side_downstream']['Other background']['mean_mbps'],row['server_side_downstream']['Other background']['peak_1s_mbps'],row['server_side_downstream']['Monitor/UI traffic']['mean_mbps'],row['server_side_downstream']['Experiment control']['mean_mbps'],row['background_packet_observations_matched_between_interfaces'],row['result_url']])
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    figure,axes=plt.subplots(3,2,figsize=(14,10),sharex=True,sharey=True)
    for row in rows[:6]:
        col=0 if row['condition']=='GCC baseline' else 1;ax=axes[row['trial']-1,col]
        for name,color,style in [('Pion media','#166b9d','-'),('Other background','#c35b16','-'),('Monitor/UI traffic','#777777','--')]:
            ax.plot(np.arange(2,27)+.5,row['server_side_downstream'][name]['mbps_per_second'][2:27],color=color,ls=style,lw=1.8,label=name)
        ax.set_title(row['condition']+' · trial '+str(row['trial']));ax.grid(alpha=.16);ax.set_xlim(2,27);ax.set_ylim(bottom=0)
        if col==0:ax.set_ylabel('Server-side ingress Mbps')
        if row['trial']==3:ax.set_xlabel('Seconds after first media packet')
    handles,labels=axes[0,0].get_legend_handles_labels();figure.legend(handles,labels,loc='upper center',bbox_to_anchor=(.5,.935),ncol=3,frameon=False)
    figure.suptitle('Traffic overlapping the Pion experiments',x=.065,ha='left',y=.985,fontsize=19)
    figure.text(.065,.947,'Same 25-second media window per run · one capture vantage · separate-time GCC and Prague cohorts',fontsize=11,color='#444444')
    figure.text(.065,.018,'Other background excludes Pion, SSH/signaling and monitor HTTP. Presence supports shared-path traffic; exact AQM and causal impact remain unverified.',fontsize=9,color='#555555')
    figure.tight_layout(rect=(.035,.05,.995,.9),h_pad=2,w_pad=2)
    figure.savefig(public/(stem+'.png'),dpi=170);figure.savefig(public/(stem+'.svg'));plt.close(figure)
    lines=['# Evidence of traffic overlapping the Pion media experiments','',
           'Additional IP traffic was present during the actual media interval, beyond the Pion flow and its signaling, SSH and monitor UI traffic. These are passive observations; no new transport run or capture was started.','',
           '| Condition | Trial | Other background entering monitor, Mbps | Delivered background, Mbps | Peak ingress over 1 s, Mbps | Background packets matched at both sides |',
           '| --- | ---: | ---: | ---: | ---: | ---: |']
    for row in rows:lines.append(f"| {row['condition']} | [{row['trial']}]({row['result_url']}) | {row['server_side_downstream']['Other background']['mean_mbps']:.3f} | {row['client_side_downstream']['Other background']['mean_mbps']:.3f} | {row['server_side_downstream']['Other background']['peak_1s_mbps']:.3f} | {row['background_packet_observations_matched_between_interfaces']:,} |")
    lines+=['',f'![Overlapping traffic]({stem}.png)','',f'[Detailed measurements and identified flows]({stem}.json) · [Summary CSV]({stem}.csv)','',
            '## Method','',report['definition'],'','Background means other IPv4/IPv6 unicast traffic directed to the same receiver Ethernet MAC. Source/destination transport identifiers and matched packet prefixes show that additional flows were present at both monitor interfaces. The parser was checked against the saved VP8 packet counts for each run.','',
            '## Interpretation','',
            'This confirms uncontrolled shared-path background traffic, including TCP connections outside the Pion peer pair. It can affect available capacity, marking, loss and delay, and its rate can differ between separate-time cohorts. It does not identify a particular application, person or controlled competitor, establish queue classification, or by itself explain each congestion event.','']
    lines+=['- '+note for note in report['limitations']]
    (public/(stem+'.md')).write_text('\n'.join(lines)+'\n')
    print('Saved',public/(stem+'.md'))

if __name__=='__main__':main()
