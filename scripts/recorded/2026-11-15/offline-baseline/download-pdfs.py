from pathlib import Path
import hashlib,json,shutil,urllib.request
folder=Path(__file__).resolve().parent
manifest=json.loads((folder/'manifest.json').read_text())
for item in manifest['files']:
    destination=folder/item['filename']
    existing=Path(item['existing_file'])
    if existing.is_file():
        data=existing.read_bytes()
        item['retrieval']='copied from retained capture artifacts'
    else:
        request=urllib.request.Request(item['source_url'],headers={'User-Agent':'Pion-GCC-offline-analysis'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=30) as response:
            data=response.read(20*1024*1024+1)
        item['retrieval']='downloaded from lld-black4'
    if len(data)>20*1024*1024 or not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:
        raise RuntimeError('Invalid or oversized PDF: '+item['filename'])
    destination.write_bytes(data)
    item['bytes']=len(data)
    item['sha256']=hashlib.sha256(data).hexdigest()
    print(item['filename'],len(data),'bytes',flush=True)
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
lines=['# GCC baseline PDFs for offline analysis','','Three GCC-only trials using the same 23.95 Mbps VP8 source and 25 Mbps initial controller target.','','Each video PDF covers the selected sender-to-Mac UDP flow, including connection-control traffic on that pair. Each feedback PDF covers the reverse RTCP and connection-control flow. Flow numbers were taken from the monitor summaries rather than assumed to match in both directions.','','| Trial | Video / downstream | Feedback / upstream |','| --- | --- | --- |']
for n in range(1,4):
    selected={item['kind']:item for item in manifest['files'] if item['trial']==n}
    lines.append('| '+str(n)+' | ['+selected['video']['filename']+']('+selected['video']['filename']+') | ['+selected['feedback']['filename']+']('+selected['feedback']['filename']+') |')
lines+=['','Original monitor URLs, flow identities and file SHA-256 hashes are recorded in manifest.json. These PDFs can be opened without a testbed connection. Raw captures and endpoint recordings remain in results/raw/<run-id>/ in the parent project.','']
(folder/'README.md').write_text('\n'.join(lines))
