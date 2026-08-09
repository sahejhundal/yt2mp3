"""Consolidate C:\\Music + iTunes media into ONE deduped folder (the iTunes
media folder), quarantining (never deleting) the weaker duplicate of each song.

Keeper rule: quality (spectral cutoff, HF) -> real album over 'Singles' ->
better/squarer cover -> bitrate -> (tie) prefer the copy already in media.

Steps:
  1. fingerprint every track in both roots (Chromaprint), cache results
  2. cluster acoustic duplicates (numpy-accelerated compare)
  3. move the loser of each duplicate group to the quarantine folder
  4. move every surviving C:\\Music track into the iTunes media folder
Nothing is deleted; everything is reversible from the quarantine folder.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import dedupe_audio as D
import audio_quality as Q

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

MEDIA = Path(r'C:/Users/chopppa/Music/iTunes/iTunes Media/Music')
EXTRA = Path(r'C:/Music')
QUAR = Path(r'C:/Users/chopppa/Desktop/music_dupes_quarantine')
CACHE = Path(r'C:/yt2mp3/.consolidate_fp_cache.json')
AUDIO = set(D.AUDIO_EXTS) | {'.mp4', '.m4p', '.m4b'}


def ff(path, key):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', f'format_tags={key}',
                        '-of', 'default=nw=1:nk=1', str(path)], capture_output=True)
    return r.stdout.decode('utf-8', 'replace').strip()


def cover_dims(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x',
                        str(path)], capture_output=True)
    try:
        w, h = r.stdout.decode().strip().split('x'); return int(w), int(h)
    except Exception:
        return (0, 0)


_QM = {}
def metrics(path, in_media):
    if path in _QM:
        return _QM[path]
    try:
        x = Q.decode_mono(path); fr, db = Q.spectrum_db(x)
        cut = round(Q.cutoff_khz(fr, db) * 2) / 2.0
        hf = round(Q.hf_ratio(fr, db), 4)
    except Exception:
        cut, hf = 0.0, 0.0
    w, h = cover_dims(path)
    album = ff(path, 'album')
    m = {
        'cut': cut, 'hf': hf,
        'album_good': 1 if album and album.strip().lower() not in ('', 'singles') else 0,
        'album': album, 'square': 1 if w and h and abs(w - h) <= 2 else 0,
        'area': w * h, 'bitrate': D.bitrate_of(path),
        'in_media': 1 if in_media else 0,
        'size': path.stat().st_size if path.exists() else 0, 'wh': f'{w}x{h}',
    }
    _QM[path] = m
    return m


def score(path, in_media):
    m = metrics(path, in_media)
    return (m['cut'], m['hf'], m['album_good'], m['square'], m['area'],
            m['bitrate'], m['in_media'], m['size'])


def compare_np(a, b, mo=30, min_overlap=80):
    la, lb = len(a), len(b)
    best = 1.0
    for off in range(-mo, mo + 1):
        s = max(0, -off); e = min(la, lb - off); ov = e - s
        if ov < min_overlap:
            continue
        x = a[s:e] ^ b[s + off:e + off]
        bits = int(np.bitwise_count(x).sum())
        ber = bits / (32.0 * ov)
        if ber < best:
            best = ber
    return 1.0 - best


def load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            return {}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--length', type=int, default=90)
    ap.add_argument('--threshold', type=float, default=0.90)
    args = ap.parse_args()
    D.check_fpcalc()

    files = []
    for root in (MEDIA, EXTRA):
        if root.exists():
            for p in sorted(root.rglob('*')):
                if p.suffix.lower() in AUDIO and p.is_file() and '._' not in p.name:
                    files.append(p)
    print(f'{len(files)} audio file(s): media + C:\\Music')

    cache = load_cache()
    prints = []
    for i, path in enumerate(files, 1):
        try:
            st = path.stat(); key = f'{path}|{st.st_size}|{int(st.st_mtime)}|{args.length}'
        except OSError:
            key = str(path)
        e = cache.get(key)
        if e:
            dur, fp = e['duration'], e['fp']
        else:
            dur, fp = D.fingerprint(path, args.length)
            if fp:
                cache[key] = {'duration': dur, 'fp': fp}
        if fp:
            prints.append((path, dur or 0.0, np.array(fp, dtype=np.uint32)))
        if i % 50 == 0 or i == len(files):
            print(f'  fp {i}/{len(files)}')
    CACHE.write_text(json.dumps({k: {'duration': v['duration'], 'fp': v['fp']}
                                 for k, v in cache.items()}))

    # sort by duration, sliding-window compare within 12s
    order = sorted(range(len(prints)), key=lambda i: prints[i][1])
    uf = D.Union(len(prints))
    sims = {}
    print('Comparing (numpy)...')
    for a in range(len(order)):
        i = order[a]; _, di, fi = prints[i]
        for b in range(a + 1, len(order)):
            j = order[b]; _, dj, fj = prints[j]
            if dj - di > 12.0:
                break
            s = compare_np(fi, fj, 30)
            if s >= args.threshold:
                uf.join(i, j); sims[(min(i, j), max(i, j))] = s
        if a % 200 == 0:
            print(f'  cmp {a}/{len(order)}')

    groups = {}
    for idx in range(len(prints)):
        groups.setdefault(uf.find(idx), []).append(idx)
    clusters = [v for v in groups.values() if len(v) > 1]
    print(f'\n{len(clusters)} duplicate group(s).')

    def inmedia(p):
        try:
            p.relative_to(MEDIA); return True
        except ValueError:
            return False

    losers = []
    report = ['Consolidation + de-dupe report',
              'Keeper: quality -> album>Singles -> cover -> bitrate -> in-media', '']
    for gi, members in enumerate(clusters, 1):
        ranked = sorted(members, key=lambda m: score(prints[m][0], inmedia(prints[m][0])),
                        reverse=True)
        keep = prints[ranked[0]][0]
        km = metrics(keep, inmedia(keep))
        report.append(f'Group {gi}: KEEP {keep}  [cut{km["cut"]} {km["bitrate"]//1000}k '
                      f'cover{km["wh"]} album={km["album"]!r}]')
        for m in ranked[1:]:
            p = prints[m][0]
            losers.append(p)
            report.append(f'    QUAR {p}')
        report.append('')
    Path('consolidate_report.txt').write_text('\n'.join(report), encoding='utf-8')

    survivors = [p for (p, _d, _f) in prints if p not in set(losers)]
    from_extra = [p for p in survivors if not inmedia(p)]
    print(f'quarantine (dupes): {len(losers)} | survivors: {len(survivors)} | '
          f'to move into media from C:\\Music: {len(from_extra)}')
    print('report: consolidate_report.txt')

    if not args.apply:
        print('\nDRY RUN - nothing moved. Re-run with --apply.')
        return

    # 1) quarantine losers (from either root)
    QUAR.mkdir(parents=True, exist_ok=True)
    qn = 0
    for p in losers:
        root = MEDIA if inmedia(p) else EXTRA
        tag = 'media' if root is MEDIA else 'C_Music'
        rel = p.relative_to(root)
        dest = QUAR / tag / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_stem(dest.stem + '_dup')
        try:
            shutil.move(str(p), str(dest)); qn += 1
        except Exception as e:
            print(f'  q-fail {p}: {e}')
    # 2) move surviving C:\Music files into media
    mv = 0
    for p in from_extra:
        if not p.exists():
            continue
        rel = p.relative_to(EXTRA)
        dest = MEDIA / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_stem(dest.stem + ' (2)')
        try:
            shutil.move(str(p), str(dest)); mv += 1
        except Exception as e:
            print(f'  mv-fail {p}: {e}')
    print(f'\nQuarantined {qn} dupe(s) -> {QUAR}')
    print(f'Moved {mv} track(s) from C:\\Music into the media folder.')
    total = sum(1 for _ in MEDIA.rglob('*') if _.suffix.lower() in AUDIO)
    print(f'Media folder now: {total} track(s) (single source of truth).')


if __name__ == '__main__':
    main()
