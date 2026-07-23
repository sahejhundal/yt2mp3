import sys
import os
import re
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import yt_dlp


def sanitize_filename(name):
    """Remove or replace characters that are invalid in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name.strip('. ')


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

    opts = {
        'format': 'bestaudio/best',
        'postprocessors': postprocessors,
        'paths': {'home': output_path},
        'outtmpl': {'default': outtmpl or '%(title)s.%(ext)s'},
        'ignoreerrors': True,
        'download_archive': archive_path or os.path.join(output_path, '.downloaded.txt'),
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        'nocheckcertificate': False,
        'quiet': quiet,
        'no_warnings': quiet,
    }

    if pp_args:
        opts['postprocessor_args'] = pp_args
    if force:
        opts.pop('download_archive', None)
    return opts


def download_youtube_audio(url, output_path='downloads', force=False):
    opts = make_ydl_opts(output_path, force=force)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def _channel_id_from_input(value):
    """Return a YouTube channel id (UC...) from a URL or raw id, else None."""
    match = re.search(r'(UC[\w-]{22})', value or '')
    return match.group(1) if match else None


def _join_artists(artists):
    """Join a ytmusicapi artists list into 'A, B' or None if empty."""
    if not artists:
        return None
    names = [a.get('name') for a in artists if a.get('name')]
    return ', '.join(names) if names else None


def _download_track(url, opts, label, progress):
    """Worker for concurrent downloads. Returns True on success."""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        with progress['lock']:
            progress['done'] += 1
            pct = progress['done'] / progress['total'] * 100
            print(f'  [{progress["done"]}/{progress["total"]}] ({pct:3.0f}%) {label}')
        return True
    except Exception as e:
        with progress['lock']:
            progress['done'] += 1
            print(f'  [{progress["done"]}/{progress["total"]}] FAILED: {label} — {e}')
        return False


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

    safe_artist = sanitize_filename(artist_name)

    print(f'\nFetching discography for {artist_name}...\n')
    artist_info = ytmusic.get_artist(artist_id)

    raw_releases = []

    need_albums_section = category in ('albums', 'eps', 'all')
    need_singles_section = category in ('singles', 'eps', 'all')

    if need_albums_section and 'albums' in artist_info:
        section = artist_info['albums']
        if 'params' in section:
            items = ytmusic.get_artist_albums(artist_id, section['params'])
        else:
            items = section.get('results', [])
        raw_releases.extend(items)

    if need_singles_section and 'singles' in artist_info:
        section = artist_info['singles']
        if 'params' in section:
            items = ytmusic.get_artist_albums(artist_id, section['params'])
        else:
            items = section.get('results', [])
        raw_releases.extend(items)

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
    release_count = 0

    for release in raw_releases:
        browse_id = release.get('browseId')
        if not browse_id:
            continue

        try:
            album_info = ytmusic.get_album(browse_id)
        except Exception as e:
            print(f'  Skipping release (fetch failed): {e}')
            continue

        release_type = album_info.get('type', 'Album')
        if release_type not in allowed_types:
            continue

        title = album_info.get('title', 'Unknown')
        year = album_info.get('year', '')
        tracks = album_info.get('tracks', [])
        if not tracks:
            continue

        safe_title = sanitize_filename(title)
        release_path = os.path.join(output_path, safe_artist, safe_title)
        os.makedirs(release_path, exist_ok=True)
        release_count += 1

        header = f'{release_type}: {title}'
        if year:
            header += f' ({year})'
        print(f'  {header}  ({len(tracks)} track(s))')

        num_tracks = len(tracks)
        for i, track in enumerate(tracks, 1):
            video_id = track.get('videoId')
            if not video_id:
                continue
            track_title_raw = track.get('title', 'Unknown')
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
                quiet=concurrent,
            )
            label = f'{title} — {track_title_raw}'
            tasks.append((url, opts, label))

    if not tasks:
        print('No tracks to download.')
        return

    # --- download phase ---
    print(f'\n{"=" * 60}')
    print(f'{len(tasks)} track(s) across {release_count} release(s)'
          f' — {jobs} concurrent worker(s)')
    print('=' * 60)

    progress = {
        'done': 0,
        'total': len(tasks),
        'lock': threading.Lock(),
    }

    if concurrent:
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
    print(f'Done! {successes}/{len(tasks)} track(s) downloaded.')
    print(f'Files saved to: {output_path}/{safe_artist}/')
    print('=' * 60)


def build_parser():
    parser = argparse.ArgumentParser(
        description='Download YouTube audio as high-quality MP3.',
        epilog='Examples:\n'
               '  python yt2mp3.py "https://youtube.com/watch?v=ID"\n'
               '  python yt2mp3.py --artist "Kendrick Lamar" --category albums\n'
               '  python yt2mp3.py -a "Frank Ocean" -c singles -f\n',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('url', nargs='?', default=None,
                        help='YouTube video URL to download, or a Music channel '
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
    return parser


if __name__ == '__main__':
    parser = build_parser()
    args = parser.parse_args()

    if args.artist:
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
    elif args.url:
        download_youtube_audio(args.url, force=args.force)
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
        else:
            download_youtube_audio(url, force=force)
