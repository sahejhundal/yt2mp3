import sys
import os
import re
import shutil
import ssl
import tempfile
import urllib.request
import argparse
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import yt_dlp

ART_ENABLED = True   # embed square album covers as the standard (disable: --no-art)


try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass


def sanitize_filename(name):
    """Remove or replace characters that are invalid in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name.strip('. ')


COOKIES_FILE = None
COOKIES_BROWSER = None  # e.g. 'chrome', 'edge', 'firefox', 'brave'


def _apply_cookies(opts):
    """Attach whichever cookie source the user configured to a yt-dlp opts dict."""
    if COOKIES_BROWSER:
        opts['cookiesfrombrowser'] = (COOKIES_BROWSER,)
        opts.update(_js_challenge_opts())
    elif COOKIES_FILE:
        opts['cookiefile'] = COOKIES_FILE
        opts.update(_js_challenge_opts())
    return opts


def _cookies_json_to_netscape(json_path):
    """Convert a browser-extension JSON cookie export to a Netscape cookies.txt.

    Returns the path to a generated temp cookies.txt. Accepts either a raw list
    of cookie dicts or an object with a 'cookies' list.
    """
    import json
    import tempfile
    with open(json_path, encoding='utf-8') as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        data = data.get('cookies', [])

    lines = ['# Netscape HTTP Cookie File', '']
    for c in data:
        domain = c.get('domain', '')
        if not domain or not c.get('name'):
            continue
        flag = 'TRUE' if domain.startswith('.') else 'FALSE'
        secure = 'TRUE' if c.get('secure') else 'FALSE'
        expiry = int(c.get('expirationDate') or c.get('expires') or 0)
        lines.append('\t'.join([
            domain, flag, c.get('path', '/'), secure,
            str(expiry), c.get('name', ''), c.get('value', ''),
        ]))

    fd, tmp = tempfile.mkstemp(prefix='ytcookies_src_', suffix='.txt')
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        out.write('\n'.join(lines) + '\n')
    return tmp


def _js_challenge_opts():
    """Options that help yt-dlp solve YouTube signature/n-sig challenges."""
    return {
        'js_runtimes': {'node': {}, 'deno': {}},
        'remote_components': ['ejs:github'],
    }


def _metadata_probe_opts():
    """Auth/challenge options for single metadata probes."""
    return _apply_cookies({'quiet': True, 'no_warnings': True})


def make_ydl_opts(output_path, force=False, outtmpl=None, archive_path=None,
                  metadata=None, quiet=False):
    postprocessors = [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '320',
    }]

    pp_args = {}

    if metadata:
        meta_args = []
        for key, value in metadata.items():
            if value:
                meta_args.extend(['-metadata', f'{key}={value}'])
        pp_args['extractaudio'] = meta_args
    else:
        postprocessors.append({'key': 'FFmpegMetadata'})

    # Write and embed the YouTube Music thumbnail so imports into iTunes/Music
    # show cover art on the MP3 files.
    postprocessors.extend([
        {'key': 'FFmpegThumbnailsConvertor', 'format': 'jpg'},
        {'key': 'EmbedThumbnail'},
    ])

    opts = {
        'format': 'bestaudio/best',
        'writethumbnail': True,
        'postprocessors': postprocessors,
        'paths': {'home': output_path},
        'outtmpl': {'default': outtmpl or '%(title)s.%(ext)s'},
        'ignoreerrors': True,
        'download_archive': archive_path or os.path.join(output_path, '.downloaded.txt'),
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        # Let yt-dlp choose clients; with a JS runtime (Deno) available it solves
        # the signature/n challenges and returns progressive audio. Forcing
        # web_safari here previously produced empty HLS downloads.
        'nocheckcertificate': False,
        'quiet': quiet,
        'no_warnings': quiet,
        'noprogress': quiet,
    }

    if pp_args:
        opts['postprocessor_args'] = pp_args
    if COOKIES_BROWSER:
        opts['cookiesfrombrowser'] = (COOKIES_BROWSER,)
        opts.update(_js_challenge_opts())
    elif COOKIES_FILE:
        # yt-dlp rewrites the cookie file with refreshed tokens on close, so a
        # shared file corrupts under concurrent workers. Give each download its
        # own private copy to read/write safely.
        import tempfile
        import shutil
        fd, tmp_cookies = tempfile.mkstemp(prefix='ytcookies_', suffix='.txt')
        os.close(fd)
        shutil.copyfile(COOKIES_FILE, tmp_cookies)
        opts['cookiefile'] = tmp_cookies
        opts.update(_js_challenge_opts())
    if force:
        opts.pop('download_archive', None)
    return opts


def download_youtube_audio(url, output_path='downloads', force=False, override_artist=None):
    if not override_artist:
        opts = make_ydl_opts(output_path, force=force)
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        return

    try:
        with yt_dlp.YoutubeDL(_metadata_probe_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        print(f'Could not read video metadata: {_short_error(e)}')
        return

    title = info.get('track') or info.get('title') or info.get('id') or 'Unknown'
    album = info.get('album') or title
    year = info.get('release_year') or str(info.get('upload_date') or '')[:4]
    safe_artist = sanitize_filename(override_artist)
    safe_album = sanitize_filename(album)
    track_title = sanitize_filename(title)
    release_path = os.path.join(output_path, safe_artist, safe_album)
    archive_path = os.path.join(output_path, safe_artist, '.downloaded.txt')
    os.makedirs(release_path, exist_ok=True)

    metadata = {
        'title': title,
        'artist': override_artist,
        'album_artist': override_artist,
        'album': album,
        'track': '1/1',
        'disc': '1/1',
        'date': str(year) if year else '',
    }
    opts = make_ydl_opts(
        release_path,
        force=force,
        outtmpl=f'01 - {track_title}.%(ext)s',
        archive_path=archive_path,
        metadata=metadata,
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    try:
        apply_square_covers(os.path.join(output_path, safe_artist))
    except Exception:
        pass


def _channel_id_from_input(value):
    """Return a YouTube channel id (UC...) from a URL or raw id, else None."""
    match = re.search(r'(UC[\w-]{22})', value or '')
    return match.group(1) if match else None


def _is_soundcloud(value):
    return 'soundcloud.com' in (value or '').lower()


def _soundcloud_kind(value):
    """Classify a SoundCloud URL: 'set', 'artist', or 'track'."""
    v = (value or '').split('?')[0].rstrip('/')
    if '/sets/' in v:
        return 'set'
    # soundcloud.com/<user>  (optionally /tracks, /popular-tracks, /albums)
    m = re.match(r'https?://(?:www\.|m\.)?soundcloud\.com/([^/]+)(/[^/]+)?$', v)
    if m:
        tail = (m.group(2) or '').lower()
        if tail in ('', '/tracks', '/popular-tracks', '/albums', '/sets', '/reposts', '/likes'):
            return 'artist'
    return 'track'


def download_soundcloud(url, output_path='downloads', force=False, jobs=4,
                        override_artist=None, album_name=None):
    """Download a SoundCloud track, set/album, or whole artist via yt-dlp.

    SoundCloud artwork is already square, so cover art comes out clean without
    the YouTube video-thumbnail problem. All the usual options apply
    (--override-artist, -j, -f).
    """
    kind = _soundcloud_kind(url)
    target = url
    if kind == 'artist' and not re.search(r'/(tracks|albums|sets|likes|reposts|popular-tracks)$', url.split('?')[0].rstrip('/')):
        target = url.split('?')[0].rstrip('/') + '/tracks'

    print(f'\nFetching SoundCloud {kind}: {url}\n')
    probe = {'quiet': True, 'no_warnings': True, 'ignoreerrors': True}
    if kind == 'track':
        probe['skip_download'] = True
    else:
        probe.update({'extract_flat': False, 'skip_download': True})
    try:
        with yt_dlp.YoutubeDL(probe) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception as e:
        print(f'Could not read SoundCloud page: {_short_error(e)}')
        return

    entries = info.get('entries')
    if entries is None:            # single track
        entries = [info]
    entries = [e for e in entries if e]

    set_title = info.get('title') if kind == 'set' else None
    uploader = info.get('uploader') or (entries[0].get('uploader') if entries else None)
    artist_label = override_artist or uploader or 'Unknown Artist'
    safe_artist = sanitize_filename(artist_label)
    release_path = os.path.join(output_path, safe_artist)
    archive_path = os.path.join(release_path, '.downloaded.txt')
    os.makedirs(release_path, exist_ok=True)

    tasks = []
    for e in entries:
        title = e.get('title') or str(e.get('id'))
        turl = e.get('webpage_url') or e.get('url')
        if not turl:
            continue
        album = album_name or set_title or 'Singles'
        safe = sanitize_filename(title)
        metadata = {
            'title': title,
            'artist': override_artist or e.get('uploader') or artist_label,
            'album_artist': override_artist or artist_label,
            'album': album,
            'date': str(e.get('release_year') or ''),
        }
        opts = make_ydl_opts(release_path, force=force, outtmpl=f'{safe}.%(ext)s',
                             archive_path=archive_path, metadata=metadata,
                             quiet=jobs > 1)
        tasks.append((turl, opts, title))

    if not tasks:
        print('No SoundCloud tracks found.')
        return
    # SoundCloud already gives square art; the YTMusic art step isn't relevant.
    global ART_ENABLED
    prev_art = ART_ENABLED
    ART_ENABLED = False
    try:
        _run_download_tasks(tasks, 1, [], [], output_path, safe_artist, jobs)
    finally:
        ART_ENABLED = prev_art


def _playlist_id_from_input(value):
    """Return a YouTube/YouTube Music playlist id from a URL or raw id."""
    value = value or ''
    match = re.search(r'[?&]list=([^&]+)', value)
    if match:
        return match.group(1)
    if re.fullmatch(r'(?:OLAK5uy_[\w-]+|VL[\w-]+|PL[\w-]+|RD[\w-]+)', value):
        return value
    return None


def _uploads_playlist_id(value):
    """Return the 'Uploads from ...' playlist id (UU...) for a channel.

    Every YouTube channel has an auto-generated uploads playlist whose id is
    the channel id with the leading 'UC' swapped for 'UU'. For a YouTube Music
    '... - Topic' channel this playlist lists the artist's ENTIRE catalogue
    (every single, album track and video), which is far more reliable than
    ytmusicapi's get_artist()/get_artist_albums() parsing that breaks whenever
    YouTube changes the artist page layout.
    """
    channel_id = _channel_id_from_input(value)
    if channel_id:
        return 'UU' + channel_id[2:]
    return None


def _normalize_title_key(title, aliases=()):
    """Return a loose comparison key for de-duplicating tracks by title."""
    text = (title or '').lower()
    for alias in aliases:
        alias = (alias or '').strip().lower()
        if alias:
            text = re.sub(r'^\s*' + re.escape(alias) + r'\s*[-\u2013\u2014~:]+\s*', '', text)
    text = re.sub(r'[\(\[\{].*?[\)\]\}]', '', text)  # drop bracketed tags
    text = re.sub(r'[^a-z0-9]+', ' ', text).strip()
    return text


def list_channel_tracks(channel_or_url):
    """Return [(video_id, title), ...] for every upload on a channel.

    Uses yt-dlp's flat playlist extraction on the channel's uploads playlist so
    it never depends on the YouTube Music artist-page layout.
    """
    uploads_id = _uploads_playlist_id(channel_or_url)
    if uploads_id:
        target = f'https://www.youtube.com/playlist?list={uploads_id}'
    else:
        target = channel_or_url

    opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'skip_download': True,
        'ignoreerrors': True,
    }
    _apply_cookies(opts)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=False)

    tracks = []
    seen = set()
    for entry in (info or {}).get('entries') or []:
        if not entry:
            continue
        vid = entry.get('id')
        title = entry.get('title')
        if not vid or not title:
            continue
        if title in ('[Private video]', '[Deleted video]', '[Unavailable video]'):
            continue
        if vid in seen:
            continue
        seen.add(vid)
        tracks.append((vid, title))
    return tracks


def download_channels_complete(channels, override_artist=None, output_path='downloads',
                               force=False, jobs=4, dedup_title=True,
                               album_name='Singles', folder_name=None):
    """Download the COMPLETE catalogue of one or more channels.

    This bypasses ytmusicapi entirely and enumerates each channel's uploads
    playlist with yt-dlp, so newly released or oddly-attached tracks (e.g. a
    single that only lives on the '... - Topic' channel) are never missed.
    When several channels belong to the same artist, pass them together so the
    catalogue is de-duplicated globally by title.
    """
    if isinstance(channels, str):
        channels = [channels]

    artist_label = folder_name or override_artist or 'Unknown Artist'
    safe_artist = sanitize_filename(artist_label)
    archive_path = os.path.join(output_path, safe_artist, '.downloaded.txt')
    release_path = os.path.join(output_path, safe_artist)
    os.makedirs(release_path, exist_ok=True)

    aliases = [override_artist or '', 'nine vicious', 'fostijs', 'sosa', 'fn']

    tasks = []
    skipped_tracks = []
    seen_ids = set()
    seen_titles = {}
    collapsed = []
    catalogue = []  # (video_id, title, channel) of everything queued

    for channel in channels:
        try:
            tracks = list_channel_tracks(channel)
        except Exception as e:
            print(f'  Warning: could not enumerate {channel}: {_short_error(e)}')
            continue

        print(f'  {channel}: {len(tracks)} upload(s) found')
        for vid, raw_title in tracks:
            if vid in seen_ids:
                continue
            seen_ids.add(vid)

            if dedup_title:
                key = _normalize_title_key(raw_title, aliases)
                if key and key in seen_titles:
                    collapsed.append((raw_title, seen_titles[key]))
                    continue
                if key:
                    seen_titles[key] = raw_title

            catalogue.append((vid, raw_title, channel))
            safe_title = sanitize_filename(raw_title)
            url = f'https://music.youtube.com/watch?v={vid}'
            outtmpl = f'{safe_title}.%(ext)s'
            metadata = {
                'title': raw_title,
                'artist': override_artist or artist_label,
                'album_artist': override_artist or artist_label,
                'album': album_name,
            }
            opts = make_ydl_opts(
                release_path,
                force=force,
                outtmpl=outtmpl,
                archive_path=archive_path,
                metadata=metadata,
                quiet=jobs > 1,
            )
            tasks.append((url, opts, raw_title))

    if collapsed:
        print(f'\n  Collapsed {len(collapsed)} duplicate-title upload(s) '
              f'(use --keep-duplicates to keep them all).')

    _run_download_tasks(
        tasks, 1, [], skipped_tracks,
        output_path, safe_artist, jobs,
    )
    return catalogue


def _join_artists(artists):
    """Join a ytmusicapi artists list into 'A, B' or None if empty."""
    if not artists:
        return None
    names = [a.get('name') for a in artists if a.get('name')]
    return ', '.join(names) if names else None


def _short_error(error):
    """Return a concise, user-readable error without dumping API internals."""
    if isinstance(error, KeyError):
        return 'YouTube Music returned an unexpected page layout'
    message = str(error).splitlines()[0].strip()
    if not message:
        return type(error).__name__
    if len(message) > 140:
        message = message[:137] + '...'
    return f'{type(error).__name__}: {message}'


def _download_track(url, opts, label, progress):
    """Worker for concurrent downloads. Returns True on success."""
    try:
        # Each worker downloads exactly one URL, so do not ignore errors here.
        # Otherwise yt-dlp can print an ERROR while we incorrectly mark success.
        worker_opts = dict(opts)
        worker_opts['ignoreerrors'] = False
        with yt_dlp.YoutubeDL(worker_opts) as ydl:
            retcode = ydl.download([url])
        if retcode:
            raise RuntimeError(f'yt-dlp exited with code {retcode}')
        with progress['lock']:
            progress['done'] += 1
            pct = progress['done'] / progress['total'] * 100
            print(f'  [{progress["done"]}/{progress["total"]}] ({pct:3.0f}%) {label}')
        return True
    except Exception as e:
        with progress['lock']:
            progress['done'] += 1
            reason = _short_error(e)
            progress['failures'].append((label, reason))
            print(f'  [{progress["done"]}/{progress["total"]}] FAILED: {label} — {reason}')
        return False


def _get_artist_section_releases(ytmusic, artist_id, section, label):
    """Return releases from an artist section without crashing on YT layout changes."""
    releases = []
    seen = set()

    def add_items(items):
        for item in items or []:
            browse_id = item.get('browseId')
            if browse_id and browse_id not in seen:
                releases.append(item)
                seen.add(browse_id)

    # get_artist() usually includes the first page already. Keep it as a safe
    # fallback because get_artist_albums() can break when YouTube returns a
    # musicShelfRenderer instead of the carousel/grid ytmusicapi expects.
    add_items(section.get('results', []))

    if section.get('params'):
        try:
            add_items(ytmusic.get_artist_albums(artist_id, section['params']))
        except Exception as e:
            reason = _short_error(e)
            if releases:
                print(f'  Warning: could not fetch more {label}; using listed results. {reason}.')
            else:
                print(f'  Warning: could not fetch {label}. {reason}.')

    return releases


def _get_artist_videos(ytmusic, artist_info):
    """Return artist music videos, including more items from the videos playlist."""
    section = artist_info.get('videos') or {}
    videos = []
    seen = set()

    def add_items(items):
        for item in items or []:
            video_id = item.get('videoId')
            if video_id and video_id not in seen:
                videos.append(item)
                seen.add(video_id)

    add_items(section.get('results', []))

    browse_id = section.get('browseId')
    if browse_id:
        try:
            playlist = ytmusic.get_playlist(browse_id, limit=1000)
            add_items(playlist.get('tracks', []))
        except Exception as e:
            reason = _short_error(e)
            if videos:
                print(f'  Warning: could not fetch more music videos; using listed results. {reason}.')
            else:
                print(f'  Warning: could not fetch music videos. {reason}.')

    return videos


def _fallback_album_from_artist_songs(release, artist_info, artist_name):
    """Build a one-track release from get_artist()['songs'] if get_album() fails."""
    songs_section = artist_info.get('songs') or {}
    songs = songs_section.get('results', []) if isinstance(songs_section, dict) else []
    browse_id = release.get('browseId')
    release_title = release.get('title') or 'Unknown'

    for song in songs:
        album = song.get('album') or {}
        album_matches = browse_id and album.get('id') == browse_id
        title_matches = song.get('title') == release_title or album.get('name') == release_title
        if not (album_matches or title_matches):
            continue

        video_id = song.get('videoId')
        if not video_id:
            continue

        artists = song.get('artists') or [{'name': artist_name, 'id': None}]
        return {
            'title': album.get('name') or release_title,
            'type': release.get('type') or 'Single',
            'year': release.get('year') or '',
            'artists': artists,
            'tracks': [{
                'videoId': video_id,
                'title': song.get('title') or release_title,
                'artists': artists,
            }],
        }

    return None


def _album_tag(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                        'format_tags=album', '-of', 'default=nw=1:nk=1', str(path)],
                       capture_output=True)
    return r.stdout.decode('utf-8', 'replace').strip()


def _cover_norm(t):
    s = (t or '').lower()
    s = re.sub(r'[\(\[\{].*?[\)\]\}]', '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s).strip()
    return s


def _embed_cover(path, cover_jpg):
    d = tempfile.mkdtemp(prefix='art_')
    tmp = os.path.join(d, os.path.basename(path))
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(path), '-i', cover_jpg,
           '-map', '0:a', '-map', '1:0', '-c:a', 'copy', '-c:v', 'mjpeg',
           '-id3v2_version', '3', '-disposition:v', 'attached_pic',
           '-metadata:s:v', 'title=Album cover',
           '-metadata:s:v', 'comment=Cover (front)', tmp]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        shutil.move(tmp, str(path))
        return True
    except Exception:
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def apply_square_covers(folder):
    """Standard art step: replace 16:9 video thumbnails with the official square
    album cover from YouTube Music when a matching release exists; otherwise
    leave the existing thumbnail untouched."""
    if not ART_ENABLED:
        return
    try:
        from ytmusicapi import YTMusic
    except ImportError:
        return
    root = folder
    files = []
    for dirpath, _dirs, names in os.walk(root):
        if '_duplicates' in dirpath.split(os.sep):
            continue
        for n in names:
            if n.lower().endswith('.mp3'):
                files.append(os.path.join(dirpath, n))
    if not files:
        return

    albums = {}
    for f in files:
        a = _album_tag(f)
        if a and a.lower() != 'singles':
            albums.setdefault(a, []).append(f)
    if not albums:
        return

    try:
        ctx = ssl.create_default_context(cafile=__import__('certifi').where())
    except Exception:
        ctx = ssl.create_default_context()
    yt = YTMusic()
    covers = {}
    for album in albums:
        url = None
        try:
            for r in yt.search(album, filter='albums', limit=5):
                if _cover_norm(r.get('title')) == _cover_norm(album):
                    alb = yt.get_album(r['browseId'])
                    th = alb.get('thumbnails') or []
                    if th:
                        url = re.sub(r'w\d+-h\d+', 'w1200-h1200', th[-1]['url'])
                    break
        except Exception:
            url = None
        path = None
        if url:
            try:
                data = urllib.request.urlopen(url, context=ctx, timeout=60).read()
                fd, path = tempfile.mkstemp(prefix='cover_', suffix='.jpg')
                with os.fdopen(fd, 'wb') as fh:
                    fh.write(data)
            except Exception:
                path = None
        covers[album] = path

    done = 0
    for album, fs in albums.items():
        cov = covers.get(album)
        if not cov:
            continue
        for f in fs:
            if _embed_cover(f, cov):
                done += 1
    if done:
        print(f'  Embedded square album covers on {done} track(s) '
              f'(others kept their thumbnail).')


def _run_download_tasks(tasks, release_count, skipped_releases, skipped_tracks,
                        output_path, safe_artist, jobs):
    if not tasks:
        print('No tracks to download.')
        return False

    print(f'\n{"=" * 60}')
    print(f'{len(tasks)} track(s) across {release_count} release(s)'
          f' — {jobs} concurrent worker(s)')
    print('=' * 60)

    progress = {
        'done': 0,
        'total': len(tasks),
        'lock': threading.Lock(),
        'failures': [],
    }

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(_download_track, url, opts, label, progress)
                for url, opts, label in tasks
            ]
            successes = sum(f.result() for f in as_completed(futures))
    else:
        successes = 0
        for url, opts, label in tasks:
            if _download_track(url, opts, label, progress):
                successes += 1

    print(f'\n{"=" * 60}')
    print(f'Done! {successes}/{len(tasks)} queued track(s) completed.')
    if skipped_releases or skipped_tracks or progress['failures']:
        print('\nSome items were not downloaded:')
        for title, browse_id, reason in skipped_releases:
            print(f'  Release skipped: {title} ({browse_id}) — {reason}')
        for release_title, track_title, reason in skipped_tracks:
            print(f'  Track skipped: {release_title} — {track_title} — {reason}')
        for label, reason in progress['failures']:
            print(f'  Download failed: {label} — {reason}')
    else:
        print('No skipped releases or failed downloads reported.')
    print(f'Files saved to: {output_path}/{safe_artist}/')
    print('=' * 60)
    # Standard art step: prefer square album covers, fall back to the thumbnail.
    try:
        apply_square_covers(os.path.join(output_path, safe_artist))
    except Exception as e:
        print(f'  (square-cover step skipped: {_short_error(e)})')
    return not (skipped_releases or skipped_tracks or progress['failures'])


def _queue_album_tracks(album_info, output_path, safe_artist, archive_path,
                        artist_name, override_artist, force, quiet,
                        tasks, skipped_tracks):
    title = album_info.get('title', 'Unknown')
    year = album_info.get('year', '')
    tracks = album_info.get('tracks', [])
    safe_title = sanitize_filename(title)
    release_path = os.path.join(output_path, safe_artist, safe_title)
    os.makedirs(release_path, exist_ok=True)

    num_tracks = len(tracks)
    for i, track in enumerate(tracks, 1):
        video_id = track.get('videoId')
        track_title_raw = track.get('title', 'Unknown')
        if not video_id:
            skipped_tracks.append((title, track_title_raw, 'No video id found'))
            print(f'    Skipping track: {title} — {track_title_raw} (no video id found)')
            continue

        track_title = sanitize_filename(track_title_raw)
        url = f'https://music.youtube.com/watch?v={video_id}'
        outtmpl = f'{i:02d} - {track_title}.%(ext)s'

        if override_artist:
            album_artists = override_artist
            track_artist = override_artist
        else:
            album_artists = _join_artists(album_info.get('artists')) or artist_name
            track_artist = _join_artists(track.get('artists')) or album_artists
        metadata = {
            'title': track_title_raw,
            'artist': track_artist,
            'album_artist': album_artists,
            'album': title,
            'track': f'{i}/{num_tracks}',
            'disc': '1/1',
            'date': year,
        }

        opts = make_ydl_opts(
            release_path,
            force=force,
            outtmpl=outtmpl,
            archive_path=archive_path,
            metadata=metadata,
            quiet=quiet,
        )
        label = f'{title} — {track_title_raw}'
        tasks.append((url, opts, label))


def download_artist_discography(artist_query, category='all', output_path='downloads',
                                force=False, jobs=4, override_artist=None):
    try:
        from ytmusicapi import YTMusic
    except ImportError:
        print('ytmusicapi is required for artist downloads.')
        print('Install with:  pip install ytmusicapi')
        sys.exit(1)

    ytmusic = YTMusic()

    channel_id = _channel_id_from_input(artist_query)
    if channel_id:
        info = ytmusic.get_artist(channel_id)
        artist_name = info.get('name') or channel_id
        artist_id = channel_id
    else:
        print(f'Searching for "{artist_query}"...')
        results = ytmusic.search(artist_query, filter='artists')
        if not results:
            print('No artists found.')
            sys.exit(1)

        print()
        for i, r in enumerate(results[:5]):
            subs = r.get('subscribers', '')
            label = f'  {i + 1}. {r["artist"]}'
            if subs:
                label += f'  ({subs})'
            print(label)

        print()
        choice = input('Select artist [1]: ').strip() or '1'
        if not choice.isdigit() or not (1 <= int(choice) <= min(5, len(results))):
            print('Invalid choice.')
            sys.exit(1)
        artist = results[int(choice) - 1]

        artist_id = artist['browseId']
        artist_name = artist['artist']

    safe_artist = sanitize_filename(override_artist or artist_name)

    print(f'\nFetching discography for {artist_name}...\n')
    artist_info = ytmusic.get_artist(artist_id)

    raw_releases = []

    need_albums_section = category in ('albums', 'eps', 'all')
    need_singles_section = category in ('singles', 'eps', 'all')

    if need_albums_section and artist_info.get('albums'):
        raw_releases.extend(
            _get_artist_section_releases(ytmusic, artist_id, artist_info['albums'], 'albums')
        )

    if need_singles_section and artist_info.get('singles'):
        raw_releases.extend(
            _get_artist_section_releases(ytmusic, artist_id, artist_info['singles'], 'singles')
        )

    if not raw_releases:
        print('No releases found for the selected category.')
        return

    allowed_types = {
        'albums': {'Album'},
        'singles': {'Single'},
        'eps': {'EP'},
        'all': {'Album', 'Single', 'EP'},
    }[category]

    archive_path = os.path.join(output_path, safe_artist, '.downloaded.txt')
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    concurrent = jobs > 1

    # --- gather phase: collect every track as a download task ---
    tasks = []  # list of (url, opts, label)
    skipped_releases = []  # list of (title, browse_id, reason)
    skipped_tracks = []  # list of (release_title, track_title, reason)
    release_count = 0

    for release in raw_releases:
        browse_id = release.get('browseId')
        if not browse_id:
            continue

        release_title = release.get('title') or browse_id
        try:
            album_info = ytmusic.get_album(browse_id)
        except Exception as e:
            album_info = _fallback_album_from_artist_songs(release, artist_info, artist_name)
            if album_info:
                print(f'  Warning: album details unavailable for "{release_title}"; using artist song listing fallback.')
            else:
                reason = _short_error(e)
                skipped_releases.append((release_title, browse_id, reason))
                print(f'  Skipping release: {release_title} ({browse_id}) — {reason}')
                continue

        release_type = album_info.get('type', 'Album')
        if release_type not in allowed_types:
            continue

        title = album_info.get('title', 'Unknown')
        year = album_info.get('year', '')
        tracks = album_info.get('tracks', [])
        if not tracks:
            skipped_releases.append((title, browse_id, 'No tracks found in release'))
            print(f'  Skipping release: {title} ({browse_id}) — no tracks found')
            continue

        release_count += 1
        header = f'{release_type}: {title}'
        if year:
            header += f' ({year})'
        print(f'  {header}  ({len(tracks)} track(s))')

        _queue_album_tracks(
            album_info, output_path, safe_artist, archive_path,
            artist_name, override_artist, force, concurrent,
            tasks, skipped_tracks,
        )

    if category == 'all':
        videos = _get_artist_videos(ytmusic, artist_info)
        if videos:
            video_album = {
                'title': 'Music Videos',
                'type': 'Videos',
                'year': '',
                'artists': [{'name': artist_name, 'id': artist_id}],
                'tracks': videos,
            }
            release_count += 1
            print(f'  Music Videos  ({len(videos)} video(s))')
            _queue_album_tracks(
                video_album, output_path, safe_artist, archive_path,
                artist_name, override_artist, force, concurrent,
                tasks, skipped_tracks,
            )

    _run_download_tasks(
        tasks, release_count, skipped_releases, skipped_tracks,
        output_path, safe_artist, jobs,
    )


def download_music_playlist(playlist_query, output_path='downloads', force=False,
                            jobs=4, override_artist=None):
    try:
        from ytmusicapi import YTMusic
    except ImportError:
        print('ytmusicapi is required for playlist/album downloads.')
        print('Install with:  pip install ytmusicapi')
        sys.exit(1)

    playlist_id = _playlist_id_from_input(playlist_query)
    if not playlist_id:
        print('Could not find a playlist id in that URL/input.')
        return

    ytmusic = YTMusic()
    print(f'\nFetching playlist/album {playlist_id}...\n')

    skipped_releases = []
    skipped_tracks = []
    album_info = None

    try:
        playlist = ytmusic.get_playlist(playlist_id, limit=1000)
    except Exception as e:
        print(f'Could not fetch playlist/album: {_short_error(e)}')
        return

    tracks = playlist.get('tracks', [])
    album_id = None
    for track in tracks:
        album = track.get('album') or {}
        if album.get('id', '').startswith('MPRE'):
            album_id = album['id']
            break

    if album_id:
        try:
            album_info = ytmusic.get_album(album_id)
        except Exception as e:
            print(f'  Warning: album details unavailable; using playlist listing. {_short_error(e)}.')

    if album_info is None:
        album_info = {
            'title': playlist.get('title') or playlist_id,
            'type': 'Playlist',
            'year': '',
            'artists': tracks[0].get('artists') if tracks else [],
            'tracks': tracks,
        }

    title = album_info.get('title', playlist.get('title') or playlist_id)
    artists = album_info.get('artists') or []
    artist_name = override_artist or _join_artists(artists) or 'Unknown Artist'
    safe_artist = sanitize_filename(artist_name)
    archive_path = os.path.join(output_path, safe_artist, '.downloaded.txt')
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)

    tracks = album_info.get('tracks', [])
    if not tracks:
        skipped_releases.append((title, album_id or playlist_id, 'No tracks found in playlist/album'))

    release_type = album_info.get('type') or 'Playlist'
    year = album_info.get('year', '')
    header = f'{release_type}: {title}'
    if year:
        header += f' ({year})'
    print(f'  {header}  ({len(tracks)} track(s))')

    tasks = []
    _queue_album_tracks(
        album_info, output_path, safe_artist, archive_path,
        artist_name, override_artist, force, jobs > 1,
        tasks, skipped_tracks,
    )

    _run_download_tasks(
        tasks, 1 if tracks else 0, skipped_releases, skipped_tracks,
        output_path, safe_artist, jobs,
    )


def build_parser():
    parser = argparse.ArgumentParser(
        description='Download YouTube audio as high-quality MP3.',
        epilog='Examples:\n'
               '  python yt2mp3.py "https://youtube.com/watch?v=ID"\n'
               '  python yt2mp3.py --artist "Kendrick Lamar" --category albums\n'
               '  python yt2mp3.py -a "Frank Ocean" -c singles -f\n'
               '  python yt2mp3.py --complete "https://music.youtube.com/channel/UCxxxx" \\\n'
               '      --override-artist "Name" --cookies cookies.json --clean --dedupe\n'
               '  python yt2mp3.py "https://soundcloud.com/user/sets/ep" --override-artist "Name"\n'
               '  python yt2mp3.py "https://soundcloud.com/user" --override-artist "Name"   # whole artist\n',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('url', nargs='?', default=None,
                        help='YouTube video/playlist/channel URL, or a SoundCloud track/'
                             'set/artist URL; also a YouTube Music channel '
                             'URL to download a discography')
    parser.add_argument('-f', '--force', action='store_true',
                        help='re-download tracks even if already downloaded')
    parser.add_argument('-a', '--artist', default=None,
                        help='search for an artist and download their discography; '
                             'also accepts a YouTube Music channel URL or UC... id')
    parser.add_argument('--override-artist', default=None,
                        help='force this name as the artist/album_artist tag on every '
                             'track (e.g. when a channel reposts another artist)')
    parser.add_argument('-c', '--category',
                        choices=['albums', 'singles', 'eps', 'all'], default='all',
                        help='which release types to download (default: all)')
    parser.add_argument('-j', '--jobs', type=int, default=4,
                        help='concurrent downloads (default: 4, use 1 for sequential)')
    parser.add_argument('--complete', action='store_true',
                        help='robustly download a channel\'s ENTIRE catalogue via its '
                             'uploads playlist (bypasses ytmusicapi; never misses tracks). '
                             'Accepts one or more channel URLs/ids as positional args.')
    parser.add_argument('channels', nargs='*', default=None,
                        help='additional channel URLs/ids for --complete mode')
    parser.add_argument('--keep-duplicates', action='store_true',
                        help='in --complete mode, keep every upload instead of collapsing '
                             'tracks that share a title across releases/channels')
    parser.add_argument('--folder', default=None,
                        help='in --complete mode, output folder name under downloads/ '
                             '(defaults to --override-artist)')
    parser.add_argument('--album', default='Singles',
                        help='in --complete mode, album tag for the tracks (default: Singles)')
    parser.add_argument('--cookies', default=None,
                        help='path to cookies for age-restricted tracks: either a Netscape '
                             'cookies.txt or a browser-extension JSON export (auto-converted). '
                             'Use youtube.com cookies from a logged-in account.')
    parser.add_argument('--cookies-from-browser', default=None,
                        metavar='BROWSER',
                        help='pull cookies straight from your logged-in browser for '
                             'age-restricted tracks (chrome, edge, firefox, brave, ...). '
                             'Easiest option - no file export needed. Close the browser first.')
    parser.add_argument('--clean', action='store_true',
                        help='after downloading, run clean_mp3_metadata.py on the output '
                             'folder (normalise titles, set artist, rename files)')
    parser.add_argument('--dedupe', action='store_true',
                        help='after downloading, run dedupe_audio.py on the output folder '
                             'and QUARANTINE acoustic duplicates (safe/reversible)')
    parser.add_argument('--dedupe-delete', action='store_true',
                        help='with --dedupe, delete duplicates instead of quarantining them')
    parser.add_argument('--no-art', action='store_true',
                        help='do NOT replace video thumbnails with square album covers '
                             '(by default the square cover is used when a release exists)')
    return parser


def _run_post_steps(output_folder, artist, do_clean, do_dedupe, dedupe_delete):
    """Optionally run metadata cleaning and acoustic de-duplication."""
    if not (do_clean or do_dedupe):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    if do_clean:
        print(f'\n=== Cleaning metadata in {output_folder} ===')
        cmd = [sys.executable, os.path.join(here, 'clean_mp3_metadata.py'),
               output_folder, '--set-artist', '--rename-files', '--apply']
        if artist:
            cmd[3:3] = ['--artist', artist]
        subprocess.run(cmd)
    if do_dedupe:
        print(f'\n=== De-duplicating audio in {output_folder} ===')
        cmd = [sys.executable, os.path.join(here, 'dedupe_audio.py'),
               output_folder, '--apply']
        if dedupe_delete:
            cmd.append('--delete')
        subprocess.run(cmd)
        print('Note: cross-folder duplicates are only found when you pass several '
              'folders to dedupe_audio.py directly.')


if __name__ == '__main__':
    parser = build_parser()
    args = parser.parse_args()

    if args.no_art:
        ART_ENABLED = False

    if args.cookies:
        cookies_path = os.path.abspath(os.path.expanduser(args.cookies))
        if not os.path.exists(cookies_path):
            print(f'Cookies file not found: {cookies_path}')
            sys.exit(1)
        # Accept a browser JSON export and convert it transparently.
        if cookies_path.lower().endswith('.json'):
            COOKIES_FILE = _cookies_json_to_netscape(cookies_path)
            print(f'Converted JSON cookies -> {COOKIES_FILE}')
        else:
            COOKIES_FILE = cookies_path

    if args.cookies_from_browser:
        COOKIES_BROWSER = args.cookies_from_browser

    if args.url and _is_soundcloud(args.url):
        download_soundcloud(
            args.url,
            force=args.force,
            jobs=max(1, args.jobs),
            override_artist=args.override_artist,
            album_name=(args.album if args.album != 'Singles' else None),
        )
    elif args.complete:
        channels = [c for c in ([args.url] + (args.channels or [])) if c]
        if not channels:
            print('Provide at least one channel URL/id with --complete.')
            sys.exit(1)
        print(f'\nComplete catalogue download for {len(channels)} channel(s)...\n')
        download_channels_complete(
            channels,
            override_artist=args.override_artist,
            force=args.force,
            jobs=max(1, args.jobs),
            dedup_title=not args.keep_duplicates,
            album_name=args.album,
            folder_name=args.folder,
        )
        out_folder = os.path.join(
            'downloads', sanitize_filename(args.folder or args.override_artist or 'Unknown Artist'))
        _run_post_steps(out_folder, args.override_artist, args.clean,
                        args.dedupe, args.dedupe_delete)
    elif args.artist:
        download_artist_discography(
            args.artist,
            category=args.category,
            force=args.force,
            jobs=max(1, args.jobs),
            override_artist=args.override_artist,
        )
    elif args.url and _channel_id_from_input(args.url):
        download_artist_discography(
            args.url,
            category=args.category,
            force=args.force,
            jobs=max(1, args.jobs),
            override_artist=args.override_artist,
        )
    elif args.url and _playlist_id_from_input(args.url):
        download_music_playlist(
            args.url,
            force=args.force,
            jobs=max(1, args.jobs),
            override_artist=args.override_artist,
        )
    elif args.url:
        download_youtube_audio(args.url, force=args.force, override_artist=args.override_artist)
    else:
        raw = input('Enter YouTube URL (add --force to re-download): ').strip()
        if not raw:
            print('No URL provided')
            sys.exit(1)
        force = args.force
        url = None
        for part in raw.split():
            if part in ('--force', '-f'):
                force = True
            elif url is None:
                url = part
            else:
                print('Please provide only one URL.')
                sys.exit(1)
        if url is None:
            print('No URL provided')
            sys.exit(1)
        if _channel_id_from_input(url):
            download_artist_discography(
                url,
                category=args.category,
                force=force,
                jobs=max(1, args.jobs),
                override_artist=args.override_artist,
            )
        elif _playlist_id_from_input(url):
            download_music_playlist(
                url,
                force=force,
                jobs=max(1, args.jobs),
                override_artist=args.override_artist,
            )
        elif _is_soundcloud(url):
            download_soundcloud(url, force=force, jobs=max(1, args.jobs),
                                override_artist=args.override_artist,
                                album_name=(args.album if args.album != 'Singles' else None))
        else:
            download_youtube_audio(url, force=force, override_artist=args.override_artist)
