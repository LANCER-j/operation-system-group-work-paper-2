"""Archive exact raw trace bytes without committing files larger than 50 MB."""
from pathlib import Path
import gzip, hashlib, json, shutil, tarfile
ROOT=Path(__file__).resolve().parent
records=[]
for raw in sorted((ROOT/'results').rglob('trace_raw.txt')):
    packed=raw.with_suffix('.txt.gz')
    if not packed.exists():
        with raw.open('rb') as src, gzip.open(str(packed),'wb',compresslevel=5) as dst:
            shutil.copyfileobj(src,dst,1024*1024)
    def digest(stream):
        h=hashlib.sha256()
        while True:
            b=stream.read(1024*1024)
            if not b:break
            h.update(b)
        return h.hexdigest()
    with raw.open('rb') as f: expected=digest(f)
    with gzip.open(str(packed),'rb') as f: actual=digest(f)
    assert expected==actual,'Compressed trace differs from original'
    records.append({'raw_path':str(raw.relative_to(ROOT)),'archive_path':str(packed.relative_to(ROOT)),
        'raw_sha256':expected,'raw_bytes':raw.stat().st_size,'archive_bytes':packed.stat().st_size})
(ROOT/'results'/'trace-archive-manifest.json').write_text(json.dumps(records,indent=2))
def filt(m):
    if m.name.endswith('/trace_raw.txt') or '/__pycache__/' in m.name or m.name.endswith('/fibo'):
        return None
    return m
with tarfile.open(str(ROOT/'results-export.tar'),'w') as out:
    out.add(str(ROOT/'results'),arcname='results',filter=filt)
    out.add(str(ROOT/'calibration'),arcname='calibration',filter=filt)
print('Verified and exported {} raw trace archives'.format(len(records)))
