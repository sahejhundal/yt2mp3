import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass


DROP_PHRASES = {
    'official music video',
    'official video',
    'official visualizer',
    'official audio',
    'music video',
    'visualizer',
    'lyric video',
    'lyrics',
    'audio',
    'video',
    'unreleased',
    'instrumental',
    'slowed + reverb',
    'slowed and reverb',
    'slowed',
    'sped up',
    'nightcore',
    'ig live',
}

FEATURE_RE = re.compile(
    r'\b(?:feat\.?|ft\.?|featuring|with|x)\b\s*.+',
    re.IGNORECASE,
)


def run(cmd):
    return subprocess.run(cmd, text=True, capture_output=True)


def ffprobe_title(path):
    result = run([
        'ffprobe', '-v', 'error',
        '-show_entries', 'format_tags=title',
        '-of', 'default=nw=1:nk=1',
        str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or 'ffprobe failed')
    title = result.stdout.strip()
    return title or path.stem


def normalize_spaces(text):
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+([,;:!?])', r'\1', text)
    text = re.sub(r'\s*[-–—~]+\s*$', '', text)
    text = re.sub(r'^\s*[-–—~]+\s*', '', text)
    return text.strip()


def strip_leading_artist(title, aliases):
    for alias in aliases:
        alias = alias.strip()
        if not alias:
            continue
        # Handles: "Artist - Song", "Artist- Song", "Artist ~ Song", "Artist: Song"
        pattern = r'^\s*' + re.escape(alias) + r'\s*(?:-|–|—|~|:)+\s*'
        new_title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        if new_title != title:
            return new_title
    return title


def clean_bracket_content(content):
    raw = normalize_spaces(content)
    low = raw.lower()

    # Keep features, but remove the brackets: "Song (feat. X)" -> "Song feat. X"
    if FEATURE_RE.search(raw):
        return raw

    # Drop common YouTube/descriptive tags.
    if low in DROP_PHRASES:
        return ''
    if any(phrase in low for phrase in DROP_PHRASES):
        return ''
    if low.startswith(('prod.', 'prod ', 'produced by')):
        return ''

    # For unknown bracket text, remove the brackets but keep the words. Use
    # --drop-all-brackets if you prefer deleting every non-feature bracket.
    return raw


def clean_title(title, aliases, drop_all_brackets=False):
    title = title.replace('_', '/')
    title = strip_leading_artist(title, aliases)

    kept_parts = []

    def bracket_repl(match):
        content = match.group(1) or match.group(2) or match.group(3) or ''
        cleaned = clean_bracket_content(content)
        if drop_all_brackets and not FEATURE_RE.search(content):
            cleaned = ''
        if cleaned:
            kept_parts.append(cleaned)
        return ' '

    # Remove (), [], and {} wrappers. Feature text is appended without brackets.
    title = re.sub(r'\(([^()]*)\)|\[([^\[\]]*)\]|\{([^{}]*)\}', bracket_repl, title)

    # Remove trailing bare tags that are not bracketed.
    title = re.sub(r'\b(?:official music video|official video|music video|official audio|visualizer|lyric video)\b', '', title, flags=re.IGNORECASE)

    title = normalize_spaces(title)
    for part in kept_parts:
        part = normalize_spaces(part)
        if part and part.lower() not in title.lower():
            title = normalize_spaces(f'{title} {part}')

    return title or normalize_spaces(strip_leading_artist(title, aliases)) or 'Unknown'


def safe_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return name.strip('. ') or 'Unknown'


def update_metadata(path, title, artist=None, album_artist=None):
    tmp_dir = Path(tempfile.mkdtemp(prefix='mp3meta_'))
    tmp_path = tmp_dir / path.name
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(path), '-map', '0', '-c', 'copy']
    cmd.extend(['-metadata', f'title={title}'])
    if artist:
        cmd.extend(['-metadata', f'artist={artist}'])
    if album_artist:
        cmd.extend(['-metadata', f'album_artist={album_artist}'])
    cmd.append(str(tmp_path))

    result = run(cmd)
    if result.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(result.stderr.strip() or 'ffmpeg failed')

    shutil.move(str(tmp_path), str(path))
    shutil.rmtree(tmp_dir, ignore_errors=True)


def unique_path(path):
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    i = 2
    while True:
        candidate = parent / f'{stem} ({i}){suffix}'
        if not candidate.exists():
            return candidate
        i += 1


def main():
    parser = argparse.ArgumentParser(
        description='Clean MP3 title metadata for YouTube Music downloads.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Examples:\n'
            '  python clean_mp3_metadata.py "C:\\Music\\Nine Vicious\\Music Videos" --artist "Nine Vicious"\n'
            '  python clean_mp3_metadata.py "C:\\Music\\Nine Vicious\\Music Videos" --artist "Nine Vicious" --apply\n'
            '  python clean_mp3_metadata.py "C:\\Music\\Nine Vicious\\Music Videos" --artist "Nine Vicious" --apply --rename-files\n'
        ),
    )
    parser.add_argument('path', help='Folder or MP3 file to clean')
    parser.add_argument('--artist', default=None, help='Artist name to remove from title and optionally set as artist tag')
    parser.add_argument('--alias', action='append', default=[], help='Additional artist/uploader prefix to remove; can be used multiple times')
    parser.add_argument('--set-artist', action='store_true', help='Also set artist and album_artist tags to --artist')
    parser.add_argument('--drop-all-brackets', action='store_true', help='Delete all non-feature bracket text instead of keeping unknown bracket text')
    parser.add_argument('--rename-files', action='store_true', help='Also rename files to keep the existing track number plus cleaned title')
    parser.add_argument('--apply', action='store_true', help='Actually modify files. Without this, only previews changes.')
    args = parser.parse_args()

    root = Path(args.path)
    if root.is_file():
        files = [root]
    else:
        files = sorted(root.rglob('*.mp3'))

    if not files:
        print('No MP3 files found.')
        return

    aliases = []
    if args.artist:
        aliases.append(args.artist)
    aliases.extend(args.alias)

    changed = 0
    failed = 0
    print('DRY RUN - no files will be changed. Add --apply to modify files.\n' if not args.apply else 'APPLYING changes...\n')

    for path in files:
        try:
            old_title = ffprobe_title(path)
            new_title = clean_title(old_title, aliases, args.drop_all_brackets)
            will_change = new_title != old_title

            new_path = path
            if args.rename_files:
                prefix = ''
                m = re.match(r'^(\d+\s*-\s*)', path.name)
                if m:
                    prefix = m.group(1)
                new_name = f'{prefix}{safe_filename(new_title)}{path.suffix}'
                new_path = unique_path(path.with_name(new_name)) if path.with_name(new_name) != path else path
                will_change = will_change or new_path != path

            if will_change:
                changed += 1
                print(f'{path.name}')
                print(f'  title: {old_title!r} -> {new_title!r}')
                if args.rename_files and new_path != path:
                    print(f'  file:  {path.name!r} -> {new_path.name!r}')

                if args.apply:
                    artist = args.artist if args.set_artist else None
                    update_metadata(path, new_title, artist=artist, album_artist=artist)
                    if args.rename_files and new_path != path:
                        path.rename(new_path)
        except Exception as e:
            failed += 1
            print(f'FAILED: {path} — {e}')

    print(f'\nDone. {changed} file(s) would change.' if not args.apply else f'\nDone. {changed} file(s) changed.')
    if failed:
        print(f'{failed} file(s) failed.')


if __name__ == '__main__':
    main()
