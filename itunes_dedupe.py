"""De-duplicate the iTunes library by acoustic fingerprint and QUARANTINE the
weaker copies (never delete). Keeper is chosen by, in order:

  1. real audio quality  (spectral cutoff, then HF detail)
  2. album tag present    (a real album beats 'Singles'/empty)
  3. better cover art     (square first, then larger area)
  4. bitrate, duration, size

Quarantined files are moved to a folder OUTSIDE the iTunes media tree so the
Music app shows them as broken links (delete those entries); the audio is safe
in the quarantine folder and fully reversible.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import dedupe_audio as D
import audio_quality as Q

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

ROOT = Path(r'C:/Users/chopppa/Music/iTunes/iTunes Media/Music')
QUAR = Path(r'C:/Users/chopppa/Desktop/iTunes_dupes_quarantine')
CACHE = Path(r'C:/yt2mp3/.itunes_fp_cache.json')
AUDIO = D.AUDIO_EXTS


def ff(path, key):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        f'format_tags={key}', '-of', 'default=nw=1:nk=1', str(path)],
                       capture_output=True)
    return r.stdout.decode('utf-8', 'replace').strip()


def cover_dims(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                        '-show_entries', 'stream=width,height', '-of', 'csv=p=0:s=x',
                        str(path)], capture_output=True)
    try:
        w, h = r.stdout.decode().strip().split('x')
        return int(w), int(h)
    except Exception:
        return (0, 0)


_QMETRICS = {}


def quality(path):
    if path in _QMETRICS:
        return _QMETRICS[path]
    try:
        x = Q.decode_mono(path)
        freqs, db = Q.spectrum_db(x)
        cut = round(Q.cutoff_khz(freqs, db) * 2) / 2.0   # round to 0.5 kHz
        hf = round(Q.hf_ratio(freqs, db), 4)
    except Exception:
        cut, hf = 0.0, 0.0
    w, h = cover_dims(path)
    album = ff(path, 'album')
    album_good = 1 if album and album.strip().lower() not in ('', 'singles') else 0
    is_square = 1 if w and h and abs(w - h) <= 2 else 0
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    m = {
        'cut': cut, 'hf': hf, 'album_good': album_good, 'album': album,
        'square': is_square, 'area': w * h, 'bitrate': D.bitrate_of(path),
        'size': size, 'wh': f'{w}x{h}',
    }
    _QMETRICS[path] = m
    return m


def score(path):
    m = quality(path)
    return (m['cut'], m['hf'], m['album_good'], m['square'], m['area'],
            m['bitrate'], m['size'])


def reason(keep, other):
    k, o = quality(keep), quality(other)
    if k['cut'] != o['cut']:
        return f"higher cutoff {k['cut']} vs {o['cut']} kHz"
    if k['hf'] != o['hf']:
        return f"more HF detail {k['hf']} vs {o['hf']}"
    if k['album_good'] != o['album_good']:
        return f"real album '{k['album']}' vs '{o['album']}'"
    if k['square'] != o['square']:
        return f"square cover ({k['wh']}) vs ({o['wh']})"
    if k['area'] != o['area']:
        return f"larger cover {k['wh']} vs {o['wh']}"
    if k['bitrate'] != o['bitrate']:
        return f"higher bitrate {k['bitrate']//1000} vs {o['bitrate']//1000} kbps"
    return "larger file"


def load_cache():
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text())
        except Exception:
            return {}
    return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true', help='quarantine dupes (else dry run)')
    ap.add_argument('--threshold', type=float, default=0.90)
    ap.add_argument('--length', type=int, default=120)
    args = ap.parse_args()

    D.check_fpcalc()
    files = [p for p in sorted(ROOT.rglob('*'))
             if p.suffix.lower() in AUDIO and p.is_file()]
    print(f'Fingerprinting {len(files)} file(s)...')
    cache = load_cache()
    prints = []
    for i, path in enumerate(files, 1):
        try:
            st = path.stat()
            key = f'{path}|{st.st_size}|{int(st.st_mtime)}|{args.length}'
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
            prints.append((path, dur or 0.0, fp))
        if i % 25 == 0 or i == len(files):
            print(f'  {i}/{len(files)}')
    CACHE.write_text(json.dumps(cache))

    n = len(prints)
    uf = D.Union(n)
    sims = {}
    print('Comparing...')
    for i in range(n):
        _, di, fi = prints[i]
        for j in range(i + 1, n):
            _, dj, fj = prints[j]
            if abs(di - dj) > 12.0:
                continue
            s = D.compare(fi, fj, 30)
            if s >= args.threshold:
                uf.join(i, j)
                sims[(i, j)] = s
    groups = {}
    for idx in range(n):
        groups.setdefault(uf.find(idx), []).append(idx)
    clusters = [sorted(v) for v in groups.values() if len(v) > 1]
    print(f'\nFound {len(clusters)} duplicate group(s).')

    report = ['iTunes de-duplication report',
              f'Keeper rule: quality -> album-over-Singles -> cover -> bitrate',
              f'Quarantine: {QUAR}', '']
    to_move = []
    for gi, members in enumerate(clusters, 1):
        ranked = sorted(members, key=lambda m: score(prints[m][0]), reverse=True)
        keep = prints[ranked[0]][0]
        km = quality(keep)
        report.append(f'Group {gi}:')
        report.append(f"  KEEP   {keep.name}  [cut {km['cut']}kHz, {km['bitrate']//1000}kbps, "
                      f"cover {km['wh']}, album '{km['album']}']")
        for m in ranked[1:]:
            p = prints[m][0]
            to_move.append(p)
            report.append(f"  QUAR   {p.name}  ->  {reason(keep, p)}")
        report.append('')

    Path('itunes_dedupe_report.txt').write_text('\n'.join(report), encoding='utf-8')
    print(f'{len(to_move)} duplicate file(s) across {len(clusters)} group(s). '
          f'Report: itunes_dedupe_report.txt')

    if not args.apply:
        print('\nDRY RUN - nothing moved. Re-run with --apply to quarantine.')
        return

    moved = 0
    for p in to_move:
        rel = p.relative_to(ROOT)
        dest = QUAR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_stem(dest.stem + '_dup')
        try:
            shutil.move(str(p), str(dest))
            moved += 1
        except Exception as e:
            print(f'  FAILED {p}: {e}')
    print(f'\nQuarantined {moved} duplicate(s) to {QUAR}')


if __name__ == '__main__':
    main()
