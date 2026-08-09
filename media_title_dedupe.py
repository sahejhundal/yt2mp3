"""Catch duplicates the strict pass missed: group media files by artist+title,
then within each group confirm they're the SAME recording acoustically before
quarantining. Generic titles (untitled/freestyle/check) with different audio
are correctly kept apart. Keeper = quality -> album>Singles -> cover -> bitrate.
"""
import argparse, re, subprocess, shutil, collections
from pathlib import Path
import numpy as np
import dedupe_audio as D
import audio_quality as Q

MEDIA = Path(r'C:/Users/chopppa/Music/iTunes/iTunes Media/Music')
QUAR = Path(r'C:/Users/chopppa/Desktop/music_dupes_quarantine/media_title')
AUD = {'.mp3', '.m4a', '.mp4', '.wav', '.flac', '.aac'}


def tag(f, k):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', f'format_tags={k}',
                        '-of', 'default=nw=1:nk=1', str(f)], capture_output=True)
    return r.stdout.decode('utf-8', 'replace').strip()


def norm(t):
    s = (t or '').lower(); s = re.sub(r'[\(\[\{].*?[\)\]\}]', '', s)
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def cover_dims(f):
    r = subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries',
                        'stream=width,height','-of','csv=p=0:s=x',str(f)],capture_output=True)
    try: w,h=r.stdout.decode().strip().split('x'); return int(w),int(h)
    except Exception: return (0,0)


def score(f):
    try:
        x=Q.decode_mono(f); fr,db=Q.spectrum_db(x)
        cut=round(Q.cutoff_khz(fr,db)*2)/2.0; hf=round(Q.hf_ratio(fr,db),4)
    except Exception: cut,hf=0.0,0.0
    w,h=cover_dims(f); album=tag(f,'album')
    ag=1 if album and album.strip().lower() not in ('','singles') else 0
    sq=1 if w and h and abs(w-h)<=2 else 0
    try: size=f.stat().st_size
    except OSError: size=0
    return (cut,hf,ag,sq,w*h,D.bitrate_of(f),size), album, f'{w}x{h}', cut


def cmp_np(a,b,mo=60,min_overlap=60):
    la,lb=len(a),len(b); best=1.0
    for off in range(-mo,mo+1):
        s=max(0,-off); e=min(la,lb-off); ov=e-s
        if ov<min_overlap: continue
        x=a[s:e]^b[s+off:e+off]
        ber=int(np.bitwise_count(x).sum())/(32.0*ov)
        if ber<best: best=ber
    return 1.0-best


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--apply',action='store_true')
    ap.add_argument('--threshold',type=float,default=0.85); a=ap.parse_args()
    D.check_fpcalc()
    files=[p for p in MEDIA.rglob('*') if p.is_file() and p.suffix.lower() in AUD and not p.name.startswith('._')]
    by=collections.defaultdict(list)
    for f in files:
        t=norm(tag(f,'title'))
        if t: by[(norm(tag(f,'artist')),t)].append(f)
    groups={k:v for k,v in by.items() if len(v)>1}
    print(f'{len(files)} files; {len(groups)} same artist+title groups to verify')

    fpcache={}
    def fp(f):
        if f not in fpcache:
            _d,arr=D.fingerprint(f,120); fpcache[f]=np.array(arr,dtype=np.uint32) if arr else None
        return fpcache[f]

    losers=[]; report=['Title-seeded acoustic de-dupe',''] ; kept_diff=0
    for (art,ti),members in sorted(groups.items()):
        # cluster acoustically
        uf=D.Union(len(members))
        for i in range(len(members)):
            fi=fp(members[i])
            if fi is None: continue
            for j in range(i+1,len(members)):
                fj=fp(members[j])
                if fj is None: continue
                if cmp_np(fi,fj)>=a.threshold: uf.join(i,j)
        clusters=collections.defaultdict(list)
        for idx in range(len(members)): clusters[uf.find(idx)].append(members[idx])
        for cl in clusters.values():
            if len(cl)<2:
                continue
            ranked=sorted(cl,key=lambda f:score(f)[0],reverse=True)
            keep=ranked[0]; _,kalb,kwh,kcut=score(keep)
            report.append(f'[{art}] {ti}  keep: {keep.relative_to(MEDIA)}  (album={kalb!r},cut{kcut},cover{kwh})')
            for f in ranked[1:]:
                losers.append(f); report.append(f'      quar: {f.relative_to(MEDIA)}')
            report.append('')
        # note groups that stayed distinct
        distinct=[c for c in clusters.values() if len(c)==1]
        if len(clusters)>1 and any(len(c)>=1 for c in distinct):
            kept_diff+=1
    Path('media_title_dedupe_report.txt').write_text('\n'.join(report),encoding='utf-8')
    print(f'true duplicate copies to quarantine: {len(losers)}')
    print(f'groups kept as DIFFERENT songs (same title, different audio): ~{kept_diff}')
    print('report: media_title_dedupe_report.txt')
    if not a.apply:
        print('\nDRY RUN - nothing moved.'); return
    QUAR.mkdir(parents=True,exist_ok=True); n=0
    for f in losers:
        rel=f.relative_to(MEDIA); dest=QUAR/rel; dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists(): dest=dest.with_stem(dest.stem+'_dup')
        try: shutil.move(str(f),str(dest)); n+=1
        except Exception as e: print('fail',f,e)
    print(f'Quarantined {n} duplicate(s) -> {QUAR}')
    print('media real audio now:', sum(1 for p in MEDIA.rglob("*") if p.is_file() and p.suffix.lower() in AUD and not p.name.startswith("._")))


if __name__=='__main__':
    main()
