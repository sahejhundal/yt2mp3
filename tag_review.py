"""Fast interactive artist tagger.

Plays each untagged song and you press ONE key to set its artist:

    d Destroy Lonely     n Nine Vicious      c Che
    p Playboi Carti      k Ken Carson        g Gunna
    t Lil Tjay           r Roddy Ricch       u Lil Uzi Vert
    l Lil Durk           2 21 Savage         o Pop Smoke
    b Lil Baby           y Young Thug        e Gunna & Roddy Ricch

    SPACE replay   s skip   x type a custom artist   q save & quit
    . (period) stop audio but stay on this song

By default it only touches files that are MISSING an artist tag. Use --all to
review every file, or pass a folder. Sets artist + album_artist only; audio and
other tags are untouched.  Needs ffplay + ffmpeg (both already installed).
"""
import argparse
import os
import subprocess
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    import msvcrt  # Windows single-keypress
except ImportError:
    msvcrt = None

MEDIA = Path(r'C:/Users/chopppa/Music/iTunes/iTunes Media/Music')
AUD = {'.mp3', '.m4a', '.mp4', '.wav', '.flac', '.aac'}

KEYMAP = {
    'd': 'Destroy Lonely', 'n': 'Nine Vicious', 'c': 'Che',
    'p': 'Playboi Carti', 'k': 'Ken Carson', 'g': 'Gunna',
    't': 'Lil Tjay', 'r': 'Roddy Ricch', 'u': 'Lil Uzi Vert',
    'l': 'Lil Durk', 'o': 'Pop Smoke',
    'b': 'Lil Baby', 'y': 'Young Thug', 'e': 'Gunna & Roddy Ricch',
    'v': '21 Savage',
}


def ff(path, key):
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', f'format_tags={key}',
                        '-of', 'default=nw=1:nk=1', str(path)], capture_output=True)
    return r.stdout.decode('utf-8', 'replace').strip()


def set_artist(path, artist):
    d = Path(tempfile.mkdtemp(prefix='tag_'))
    tmp = d / path.name
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(path), '-map', '0', '-c', 'copy',
           '-metadata', f'artist={artist}', '-metadata', f'album_artist={artist}', str(tmp)]
    subprocess.run(cmd, check=True)
    shutil.move(str(tmp), str(path))
    shutil.rmtree(d, ignore_errors=True)


def getkey():
    if msvcrt:
        ch = msvcrt.getwch()
        return ch
    return sys.stdin.read(1)


def _atempo(speed):
    if speed >= 3:
        return 'atempo=2.0,atempo=1.5'
    if speed >= 2:
        return 'atempo=2.0'
    return None


def play(path, speed=1.0, start=0.0):
    cmd = ['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet']
    if start and start > 0.1:
        cmd += ['-ss', f'{start:.2f}']
    af = _atempo(speed)
    if af:
        cmd += ['-af', af]
    cmd.append(str(path))
    return subprocess.Popen(cmd)


def stop(proc):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass


def legend(default=None):
    print('  keys: ' + '  '.join(f'{k}={v}' for k, v in KEYMAP.items()))
    line = '  1/2/3=speed 1x/2x/3x  SPACE=replay  s=skip  x=custom  .=stop  q=save & quit'
    if default:
        line = f'  ENTER={default}' + line
    print(line)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root', nargs='?', default=str(MEDIA))
    ap.add_argument('--all', action='store_true', help='review every file, not just missing-artist')
    ap.add_argument('--list', default='retag_queue.txt',
                    help='text file of paths to review (default: retag_queue.txt if present). '
                         'Skips the slow full-library scan.')
    ap.add_argument('--default', default=None,
                    help='artist applied when you press ENTER (fast-confirm). '
                         'e.g. --default "Destroy Lonely"')
    args = ap.parse_args()
    root = Path(args.root)

    todo = None
    if args.list and os.path.exists(args.list) and not args.all:
        raw = [Path(ln.strip()) for ln in open(args.list, encoding='utf-8')
               if ln.strip() and os.path.exists(ln.strip())]
        # RESUME: skip ones already tagged (so restarting continues where you left off)
        todo = [p for p in raw if not ff(p, 'artist')]
        skipped = len(raw) - len(todo)
        print(f'Loaded {len(raw)} from {args.list}; {skipped} already tagged -> '
              f'resuming with {len(todo)} remaining.')
    if todo is None:
        print('Scanning library (this can take a moment)...')
        files = [p for p in sorted(root.rglob('*'))
                 if p.is_file() and p.suffix.lower() in AUD and not p.name.startswith('._')]
        todo = [p for p in files if args.all or not ff(p, 'artist')]
    root = MEDIA
    print(f'{len(todo)} track(s) to review.\n')
    legend(args.default)
    print()

    i = 0
    done = 0
    speed = 1.0
    while i < len(todo):
        f = todo[i]
        title = ff(f, 'title') or f.stem
        cur = ff(f, 'artist')
        hint = f'  [ENTER={args.default}]' if args.default else ''
        print(f'[{i+1}/{len(todo)}] {title}    (folder: {f.relative_to(root).parts[0]}'
              + (f' | current artist: {cur}' if cur else '') + f')  speed={speed:g}x' + hint)
        base = 0.0            # song position (s) captured at last (re)launch
        t0 = time.time()
        proc = play(f, speed, 0.0)
        def songpos():
            return base + (time.time() - t0) * speed
        while True:
            ch = getkey()
            if ch in ('1', '2', '3'):
                pos = songpos(); speed = float(ch); base = pos; t0 = time.time()
                stop(proc); proc = play(f, speed, pos)
                print(f'   speed = {speed:g}x (resumed at {pos:0.0f}s)'); continue
            if ch in ('\r', '\n') and args.default:
                stop(proc); set_artist(f, args.default); done += 1
                print(f'   set artist = {args.default}\n'); i += 1; break
            if ch == ' ':
                base = 0.0; t0 = time.time(); stop(proc); proc = play(f, speed, 0.0); continue
            if ch == '.':
                base = songpos(); stop(proc); continue
            if ch in ('q', '\x03'):
                stop(proc); print(f'\nSaved. Tagged {done} track(s). Bye.'); return
            if ch == 's':
                stop(proc); print('   skipped\n'); i += 1; break
            if ch == 'x':
                stop(proc)
                name = input('   type artist name: ').strip()
                if name:
                    set_artist(f, name); done += 1; print(f'   set artist = {name}\n')
                    i += 1
                break
            if ch in KEYMAP:
                stop(proc)
                set_artist(f, KEYMAP[ch]); done += 1
                print(f'   set artist = {KEYMAP[ch]}\n')
                i += 1
                break
            # unknown key -> show legend again
            print('   ? unknown key'); legend()
    print(f'\nAll done. Tagged {done} track(s).')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nInterrupted. Progress saved (each tag is written immediately).')
