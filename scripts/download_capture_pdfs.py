#!/usr/bin/env python3
"""Retrieve or verify selected offline capture PDFs using a retained manifest.

Filenames and checksums come from the explicitly supplied manifest; no monitor
capture is started. Existing valid copies are reused and never overwritten.
"""
import argparse
import hashlib
import http.client
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]


def retrieve(manifest_path):
    """Validate each selected PDF, preferring a matching retained local artifact."""
    manifest_path=Path(manifest_path);manifest=json.loads(manifest_path.read_text());results=[]
    for item in manifest['files']:
        name=item['filename']
        if Path(name).name!=name or not name.endswith('.pdf'):raise ValueError('unsafe PDF filename')
        destination=manifest_path.parent/name
        if destination.exists(): data=destination.read_bytes();origin='existing offline copy'
        else:
            existing=Path(item.get('existing_file',''))
            if not existing.is_file() and 'results/' in str(existing):existing=ROOT/('results/'+str(existing).split('results/',1)[1])
            if existing.is_file():data=existing.read_bytes();origin='retained local artifact'
            else:
                url=urlsplit(item['source_url'])
                if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:raise ValueError('invalid source URL')
                cls=http.client.HTTPSConnection if url.scheme=='https' else http.client.HTTPConnection
                conn=cls(url.hostname,url.port,timeout=30)
                try:
                    conn.request('GET',url.path+('?' + url.query if url.query else ''));response=conn.getresponse()
                    if response.status!=200:raise ValueError('PDF HTTP '+str(response.status))
                    data=response.read(20*1024*1024+1);origin='downloaded selected PDF'
                finally:conn.close()
        if len(data)>20*1024*1024 or not data.startswith(b'%PDF-') or b'%%EOF' not in data[-2048:]:raise ValueError('invalid or oversized PDF: '+name)
        digest=hashlib.sha256(data).hexdigest()
        if item.get('sha256') and digest!=item['sha256']:raise ValueError('PDF hash mismatch: '+name)
        if not destination.exists():
            with destination.open('xb') as stream:stream.write(data)
        results.append(dict(filename=name,bytes=len(data),sha256=digest,source=origin))
    return results


def main():
    """Fetch only the PDFs selected by a user-supplied manifest."""
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--manifest',required=True);args=parser.parse_args()
    print(json.dumps(retrieve(ROOT/args.manifest),indent=2))


if __name__=='__main__':main()
