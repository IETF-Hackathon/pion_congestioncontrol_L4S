#!/usr/bin/env python3
"""Inventory the active checkout and reversibly archive unreferenced old artifacts.

Only the 'archive' action moves files. Runtime code, current tooling, referenced
evidence, today's run artifacts and media are retained. Private raw artifacts
go under ignored archive/local; each move has a SHA-256 inventory and reason.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def text_references():
    """Read active source/docs plus today's run manifests, excluding frozen copies."""
    paths=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    texts=[]
    for name in paths:
        p=ROOT/name
        if name.startswith(('archive/','scripts/recorded/')) or not p.is_file() or p.stat().st_size>2*1024*1024:
            continue
        if p.suffix in ('.md','.json','.html','.py','.sh','.mjs','.go','.yaml','.yml','.dot','.svg'):
            texts.append(p.read_text(errors='replace'))
    for folder in (ROOT/'results/raw').iterdir():
        if folder.is_dir() and re.search(r'20261115T(?:0[89]|1[0-9]|2[0-3])',folder.name):
            for p in folder.glob('*.json'):
                if p.stat().st_size<2*1024*1024:texts.append(p.read_text(errors='replace'))
    return '\n'.join(texts)


def candidates():
    """Select stale unreferenced raw attempts and unused image/source families."""
    references=text_references();result=[]
    raw=ROOT/'results/raw'
    for p in sorted(raw.iterdir()):
        match=re.search(r'(202611\d\dT\d{6})',p.name)
        if p.is_dir() and match and match[1]<'20261115T080000' and p.name not in references:
            result.append((p,Path('archive/local/retired-20261115/raw')/p.name,'Old run/attempt not referenced by active pages, tools, reports or today’s run manifests'))
    images=ROOT/'docs/images'
    for p in sorted(images.iterdir()):
        if not p.is_file() or p.suffix not in ('.png','.svg','.jpg','.dot','.md'):continue
        # A diagram source stays with any referenced sibling image of that stem.
        siblings=[x for x in images.iterdir() if x.stem==p.stem]
        if not any(x.name in references for x in siblings):
            result.append((p,Path('archive/retired-assets-20261115/docs-images')/p.name,'Unreferenced diagram asset/source family'))
    for p in ROOT.glob('Unconfirmed *.crdownload'):
        try:
            check=subprocess.run(['lsof','-t','--',str(p)],capture_output=True,text=True,timeout=8)
            if check.returncode==1:
                result.append((p,Path('archive/local/retired-20261115/downloads')/p.name,'Unreferenced incomplete download, no open file handle observed'))
        except (OSError,subprocess.SubprocessError):pass
    old=ROOT/'results/offline/pion-gcc-baseline-25m-20261115T181607Z/download-pdfs.py'
    if old.exists() and (ROOT/'scripts/download_capture_pdfs.py').exists():
        result.append((old,Path('archive/retired-tools-20261115/download-pdfs.py'),'Superseded by scripts/download_capture_pdfs.py; exact used copy retained under scripts/recorded'))
    return result


def inventory(path):
    """Hash moved files without placing their contents or credentials in the report."""
    files=[path] if path.is_file() else sorted(p for p in path.rglob('*') if p.is_file() and not p.is_symlink())
    rows=[]
    for p in files:
        digest=hashlib.sha256()
        with p.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
        rows.append(dict(path=str(p.relative_to(ROOT)),bytes=p.stat().st_size,sha256=digest.hexdigest()))
    return rows


def main():
    """Print a reference-based plan or perform the requested reversible archive sweep."""
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('plan','archive'))
    args=parser.parse_args();rows=[]
    manifest_path=ROOT/'archive/cleanup-20261115.json'
    if args.action=='archive' and manifest_path.exists():raise ValueError('archive manifest already exists; preserve it before another sweep')
    for source,destination,reason in candidates():
        if (ROOT/destination).exists():raise ValueError('archive destination exists: '+str(destination))
        row=dict(source=str(source.relative_to(ROOT)),destination=str(destination),reason=reason)
        if args.action=='archive':
            row['files']=inventory(source)
            target=ROOT/destination;target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(source),str(target))
        rows.append(row)
    result=dict(created_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),action=args.action,moves=rows,
                retained='Current runtime and tools, all media, referenced reports/evidence, and today’s raw artifacts')
    if args.action=='archive':
        manifest_path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
