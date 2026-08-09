import re
import subprocess
import shutil
import tempfile
from pathlib import Path

from ytmusicapi import YTMusic

NV_CHANNELS = [
    'UCjEik_M2OnnNuiXkqLD580g',  # Fostijs main
    'UCC_-Q2GDGmcTd5s5jaN5a3A',  # Fostijs topic
    'UCrxi5Gzsa21BZyfvcWTAsew',  # nv vault
    'UCn9xaCrcgSc0dQk8K6sbrWA',  # fn
    'UCAsV1-xpY8BGzF72xblkDag',  # sosa
    'UCvzAxEEMi7iJvoIPFwx-1fQ',  # prodbypatrick
]
CHE_CHANNELS = ['UC8XF8DliaPKpOkvXSJC0tkQ']

TYPE_RANK = {'Album': 3, 'EP': 2, 'Single': 1, '': 0, None: 0}


def norm(t):
    s = (t or '').lower()
    for a in ('nine vicious', 'fostijs', 'sosa', 'fn', 'che', 'chechive', 'bass killer'):
        s = re.sub(r'^\s*' + re.escape(a) + r'\s*[-\u2010-\u2015\u2212~:/]+\s*', '', s)
    s = re.sub(r'[\(\[\{].*?[\)\]\}]', '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    return s


def build_album_map(channels):
    yt = YTMusic()
    releases = {}   # browseId -> type
    for ch in channels:
        try:
            info = yt.get_artist(ch)
        except Exception as e:
            print(f'  get_artist {ch} failed: {e}')
            continue
        for sec in ('albums', 'singles'):
            s = info.get(sec) or {}
            for r in (s.get('results') or []):
                bid = r.get('browseId')
                if bid:
                    releases.setdefault(bid, r.get('type') or ('Album' if sec == 'albums' else 'Single'))
    print(f'  {len(releases)} release(s) to inspect...')
    amap = {}   # norm(title) -> (album, year, type)
    for bid, rtype in releases.items():
        try:
            alb = yt.get_album(bid)
        except Exception:
            continue
        album = alb.get('title') or ''
        year = alb.get('year') or ''
        atype = alb.get('type') or rtype or ''
        for tr in alb.get('tracks', []):
            k = norm(tr.get('title'))
            if not k:
                continue
            cand = (album, year, atype)
            cur = amap.get(k)
            if cur is None or TYPE_RANK.get(atype, 0) > TYPE_RANK.get(cur[2], 0):
                amap[k] = cand
    return amap


def set_album(f, album, year):
    tmpd = Path(tempfile.mkdtemp(prefix='alb_'))
    tmp = tmpd / f.name
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(f), '-map', '0', '-c', 'copy',
           '-metadata', f'album={album}']
    if year:
        cmd += ['-metadata', f'date={year}']
    cmd.append(str(tmp))
    subprocess.run(cmd, check=True)
    shutil.move(str(tmp), str(f))
    shutil.rmtree(tmpd, ignore_errors=True)


def title_of(f):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags=title',
                        '-of', 'default=nw=1:nk=1', str(f)], capture_output=True)
    return r.stdout.decode('utf-8', 'replace').strip() or f.stem


def backfill(folder, channels):
    print(f'\n=== {folder} ===')
    amap = build_album_map(channels)
    files = sorted((Path('downloads') / folder).glob('*.mp3'))
    hit = miss = 0
    misses = []
    for f in files:
        k = norm(title_of(f))
        # keep the real Couple Days album already tagged
        if f.stem.lower() in ('pain', 'washed up', 'feelings', 'french montana'):
            pass
        m = amap.get(k)
        if m and m[0]:
            set_album(f, m[0], m[1])
            hit += 1
        else:
            miss += 1
            misses.append(f.stem)
    print(f'  album set on {hit} file(s); left as-is on {miss}.')
    if misses:
        print('  no album match (kept Singles):')
        for x in sorted(misses):
            print('    -', x)


if __name__ == '__main__':
    backfill('Nine Vicious', NV_CHANNELS)
    backfill('Che', CHE_CHANNELS)
