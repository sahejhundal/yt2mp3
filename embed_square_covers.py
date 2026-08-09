"""Embed proper SQUARE album covers from YouTube Music.

For every track tagged with a real album, fetch that release's official square
artwork (up to 1200x1200) and embed it, replacing the 16:9 video thumbnail that
yt-dlp originally embedded. Tracks with no real release (album == 'Singles',
or no match) keep their existing thumbnail -- unless --crop-fallback is given,
in which case their current art is center-cropped to a square.
"""
import argparse
import re
import ssl
import subprocess
import tempfile
import urllib.request
from pathlib import Path

import certifi
from ytmusicapi import YTMusic

CTX = ssl.create_default_context(cafile=certifi.where())

NV_CHANNELS = [
    'UCjEik_M2OnnNuiXkqLD580g', 'UCC_-Q2GDGmcTd5s5jaN5a3A',
    'UCrxi5Gzsa21BZyfvcWTAsew', 'UCn9xaCrcgSc0dQk8K6sbrWA',
    'UCAsV1-xpY8BGzF72xblkDag', 'UCvzAxEEMi7iJvoIPFwx-1fQ',
]
CHE_CHANNELS = ['UC8XF8DliaPKpOkvXSJC0tkQ']
# Couple Days album (skratpakk) so those tracks also get their real cover.
EXTRA_ALBUM_IDS = ['MPREb_sYrJp5yFI3u']


def norm(t):
    s = (t or '').lower()
    s = re.sub(r'[\(\[\{].*?[\)\]\}]', '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    return s


def best_square_url(thumbs):
    if not thumbs:
        return None
    # thumbnails are square already; take the biggest and request 1200.
    url = thumbs[-1]['url']
    return re.sub(r'w\d+-h\d+', 'w1200-h1200', url)


def build_cover_map(channels, extra_album_ids=()):
    yt = YTMusic()
    release_ids = set(extra_album_ids)
    for ch in channels:
        try:
            info = yt.get_artist(ch)
        except Exception as e:
            print(f'  get_artist {ch} failed: {e}')
            continue
        for sec in ('albums', 'singles'):
            for r in ((info.get(sec) or {}).get('results') or []):
                if r.get('browseId'):
                    release_ids.add(r['browseId'])
    print(f'  inspecting {len(release_ids)} release(s) for covers...')
    cmap = {}   # norm(album title) -> url
    tmap = {}   # norm(track title) -> url   (secondary match)
    for bid in release_ids:
        try:
            alb = yt.get_album(bid)
        except Exception:
            continue
        url = best_square_url(alb.get('thumbnails'))
        if not url:
            continue
        cmap.setdefault(norm(alb.get('title')), url)
        for tr in alb.get('tracks', []):
            tmap.setdefault(norm(tr.get('title')), url)
    return cmap, tmap


_CACHE = {}


def fetch(url):
    if url not in _CACHE:
        data = urllib.request.urlopen(url, context=CTX, timeout=60).read()
        fd, p = tempfile.mkstemp(prefix='cover_', suffix='.jpg')
        with open(p, 'wb') as f:
            f.write(data)
        _CACHE[url] = p
    return _CACHE[url]


def tag(f, key):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        f'format_tags={key}', '-of', 'default=nw=1:nk=1', str(f)],
                       capture_output=True)
    return r.stdout.decode('utf-8', 'replace').strip()


def embed(f, cover_jpg):
    d = Path(tempfile.mkdtemp(prefix='art_'))
    tmp = d / f.name
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(f), '-i', str(cover_jpg),
           '-map', '0:a', '-map', '1:0', '-c:a', 'copy', '-c:v', 'mjpeg',
           '-id3v2_version', '3', '-disposition:v', 'attached_pic',
           '-metadata:s:v', 'title=Album cover',
           '-metadata:s:v', 'comment=Cover (front)', str(tmp)]
    subprocess.run(cmd, check=True)
    import shutil
    shutil.move(str(tmp), str(f))
    shutil.rmtree(d, ignore_errors=True)


def crop_square(f):
    d = Path(tempfile.mkdtemp(prefix='art_'))
    tmp = d / f.name
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(f),
           '-map', '0:a', '-map', '0:v:0', '-c:a', 'copy', '-c:v', 'mjpeg',
           '-vf', "crop='min(iw,ih)':'min(iw,ih)'",
           '-id3v2_version', '3', '-disposition:v', 'attached_pic',
           '-metadata:s:v', 'comment=Cover (front)', str(tmp)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        shutil.rmtree(d, ignore_errors=True)
        return False
    import shutil
    shutil.move(str(tmp), str(f))
    shutil.rmtree(d, ignore_errors=True)
    return True


def run(folder, channels, extra_album_ids=(), crop_fallback=False):
    print(f'\n=== {folder} ===')
    cmap, tmap = build_cover_map(channels, extra_album_ids)
    files = sorted((Path('downloads') / folder).glob('*.mp3'))
    square = kept = cropped = 0
    for f in files:
        album = tag(f, 'album')
        url = None
        if album and album.lower() != 'singles':
            url = cmap.get(norm(album))
        if not url:
            url = tmap.get(norm(tag(f, 'title')))
        if url:
            try:
                embed(f, fetch(url))
                square += 1
                continue
            except Exception as e:
                print(f'  embed failed {f.name}: {e}')
        # fallback
        if crop_fallback and crop_square(f):
            cropped += 1
        else:
            kept += 1
    print(f'  square cover embedded: {square} | '
          f'{"cropped-square: "+str(cropped)+" | " if crop_fallback else ""}'
          f'kept video thumb: {kept}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--crop-fallback', action='store_true',
                    help='center-crop the video thumbnail to a square when no '
                         'official square cover exists (default: keep as-is)')
    args = ap.parse_args()
    run('Nine Vicious', NV_CHANNELS, EXTRA_ALBUM_IDS, args.crop_fallback)
    run('Che', CHE_CHANNELS, crop_fallback=args.crop_fallback)
