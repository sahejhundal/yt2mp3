import sys
import os
import re
import argparse
import yt_dlp


def sanitize_filename(name):
    """Remove or replace characters that are invalid in filenames."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name.strip('. ')


def make_ydl_opts(output_path, force=False, outtmpl=None, archive_path=None):
    opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }],
        'outtmpl': outtmpl or f'{output_path}/%(title)s.%(ext)s',
        'ignoreerrors': True,
        'download_archive': archive_path or f'{output_path}/.downloaded.txt',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
        'nocheckcertificate': False,
        'quiet': False,
        'no_warnings': False,
    }
    if force:
        opts.pop('download_archive', None)
    return opts


def download_youtube_audio(url, output_path='downloads', force=False):
    opts = make_ydl_opts(output_path, force=force)
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


def download_artist_discography(artist_query, category='all', output_path='downloads', force=False):
    try:
        from ytmusicapi import YTMusic
    except ImportError:
        print('ytmusicapi is required for artist downloads.')
        print('Install with:  pip install ytmusicapi')
        sys.exit(1)

    ytmusic = YTMusic()

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
    total_tracks = 0
    total_releases = 0

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

        header = f'{release_type}: {title}'
        if year:
            header += f' ({year})'
        print(f'\n{"=" * 60}')
        print(header)
        print(f'{len(tracks)} track(s)')
        print('=' * 60)

        for i, track in enumerate(tracks, 1):
            video_id = track.get('videoId')
            if not video_id:
                continue
            track_title = sanitize_filename(track.get('title', 'Unknown'))
            url = f'https://music.youtube.com/watch?v={video_id}'
            outtmpl = os.path.join(release_path, f'{i:02d} - {track_title}.%(ext)s')

            opts = make_ydl_opts(
                release_path,
                force=force,
                outtmpl=outtmpl,
                archive_path=archive_path,
            )
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            total_tracks += 1

        total_releases += 1

    print(f'\n{"=" * 60}')
    print(f'Done! {total_releases} release(s), {total_tracks} track(s) processed.')
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
                        help='YouTube URL to download')
    parser.add_argument('-f', '--force', action='store_true',
                        help='re-download tracks even if already downloaded')
    parser.add_argument('-a', '--artist', default=None,
                        help='search for an artist and download their discography')
    parser.add_argument('-c', '--category',
                        choices=['albums', 'singles', 'eps', 'all'], default='all',
                        help='which release types to download (default: all)')
    return parser


if __name__ == '__main__':
    parser = build_parser()
    args = parser.parse_args()

    if args.artist:
        download_artist_discography(
            args.artist,
            category=args.category,
            force=args.force,
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
        download_youtube_audio(url, force=force)
