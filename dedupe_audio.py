"""Acoustic-fingerprint de-duplicator for a music library.

Finds tracks that are the SAME recording even when they have different
filenames, tags, bitrate, container or came from different YouTube channels.
It does this with Chromaprint (the AcoustID fingerprinter) and compares the
raw fingerprints locally -- no internet, no database -- so it also works for
unreleased/leaked tracks. Remixes, live takes and "slowed + reverb" edits are
genuinely different recordings and are correctly kept apart.

Requires the `fpcalc` binary from Chromaprint:  brew install chromaprint
Optional (better keeper choice): mutagen  ->  pip install mutagen

Usage:
    python dedupe_audio.py "downloads/Nine Vicious"            # dry run report
    python dedupe_audio.py "downloads" --apply                 # quarantine dupes
    python dedupe_audio.py "downloads" --apply --delete        # delete dupes
    python dedupe_audio.py "downloads" --threshold 0.92        # stricter match
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass


AUDIO_EXTS = {'.mp3', '.m4a', '.aac', '.flac', '.wav', '.ogg', '.opus', '.wma', '.aiff'}
CACHE_NAME = '.fingerprints.json'


def check_fpcalc():
    if shutil.which('fpcalc') is None:
        print('ERROR: fpcalc not found. Install Chromaprint:')
        print('  macOS:         brew install chromaprint')
        print('  Debian/Ubuntu: sudo apt install libchromaprint-tools')
        sys.exit(1)


def fingerprint(path, length=120):
    """Return (duration_seconds, [uint32, ...]) for a file, or (None, None)."""
    try:
        out = subprocess.run(
            ['fpcalc', '-raw', '-json', '-length', str(length), str(path)],
            text=True, capture_output=True, timeout=180,
        )
        if out.returncode != 0:
            return None, None
        data = json.loads(out.stdout)
        fp = data.get('fingerprint')
        if isinstance(fp, str):
            fp = [int(x) for x in fp.split(',') if x]
        return float(data.get('duration') or 0.0), fp or None
    except Exception:
        return None, None


def bitrate_of(path):
    """Best-effort bitrate (bits/s) for keeper selection; 0 if unknown."""
    try:
        from mutagen import File as MutagenFile
        mf = MutagenFile(str(path))
        if mf is not None and getattr(mf, 'info', None) is not None:
            return int(getattr(mf.info, 'bitrate', 0) or 0)
    except Exception:
        pass
    return 0


def compare(a, b, max_offset=30, min_overlap=80):
    """Return acoustic similarity in [0, 1] between two raw fingerprints.

    Slides one fingerprint against the other by up to +/-max_offset frames
    (~1 frame = 0.124 s) and reports the best (lowest) bit-error rate, so a
    small difference in leading silence does not break a real match.
    """
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0.0
    best_ber = 1.0
    for offset in range(-max_offset, max_offset + 1):
        start = max(0, -offset)
        end = min(la, lb - offset)
        overlap = end - start
        if overlap < min_overlap:
            continue
        diff_bits = 0
        for i in range(start, end):
            diff_bits += (a[i] ^ b[i + offset]).bit_count()
        ber = diff_bits / (32.0 * overlap)
        if ber < best_ber:
            best_ber = ber
    return 1.0 - best_ber


class Union:
    """Tiny union-find for clustering matched files."""
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def join(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def load_cache(root):
    path = root / CACHE_NAME
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_cache(root, cache):
    try:
        (root / CACHE_NAME).write_text(json.dumps(cache))
    except Exception:
        pass


def keeper_score(path, prefer=()):
    """Higher is better: preferred folder first, then bitrate, then larger file."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    pref_rank = 0
    sp = str(path)
    for i, token in enumerate(prefer):
        if token and token in sp:
            pref_rank = len(prefer) - i  # earlier token => higher rank
            break
    return (pref_rank, bitrate_of(path), size)


def main():
    parser = argparse.ArgumentParser(
        description='Find and remove duplicate audio by acoustic fingerprint.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('roots', nargs='+', help='one or more folders to scan (recursively). '
                                                 'Pass several to de-dupe across artists/folders.')
    parser.add_argument('--threshold', type=float, default=0.90,
                        help='min acoustic similarity to call two files the same '
                             'recording (default 0.90; raise for stricter matching)')
    parser.add_argument('--max-offset', type=int, default=30,
                        help='frames of start-offset tolerance (~0.124 s each, default 30)')
    parser.add_argument('--duration-slack', type=float, default=12.0,
                        help='skip comparing files whose durations differ by more than '
                             'this many seconds (default 12)')
    parser.add_argument('--length', type=int, default=120,
                        help='seconds of audio to fingerprint per file (default 120)')
    parser.add_argument('--apply', action='store_true',
                        help='actually act on duplicates (otherwise dry-run report only)')
    parser.add_argument('--delete', action='store_true',
                        help='with --apply, delete duplicates instead of quarantining them')
    parser.add_argument('--quarantine', default=None,
                        help='folder to move duplicates into (default: <root>/_duplicates)')
    parser.add_argument('--prefer', default='',
                        help='comma-separated path substrings; a copy whose path matches an '
                             'earlier token is preferred as the one to KEEP '
                             '(e.g. --prefer "Nine Vicious/,Vault")')
    parser.add_argument('--no-cache', action='store_true',
                        help='do not read/write the fingerprint cache')
    args = parser.parse_args()

    check_fpcalc()
    roots = [Path(r) for r in args.roots]
    for r in roots:
        if not r.exists():
            print(f'Path not found: {r}')
            sys.exit(1)

    cache_root = roots[0]
    quarantine = Path(args.quarantine) if args.quarantine else cache_root / '_duplicates'

    files = []
    seen = set()
    for r in roots:
        for p in sorted(r.rglob('*')):
            if p.suffix.lower() not in AUDIO_EXTS:
                continue
            if quarantine == p.parent or quarantine in p.parents:
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            files.append(p)
    files.sort()
    if not files:
        print('No audio files found.')
        return

    cache = {} if args.no_cache else load_cache(cache_root)
    print(f'Fingerprinting {len(files)} file(s)...')

    prints = []  # (path, duration, fingerprint)
    for i, path in enumerate(files, 1):
        try:
            st = path.stat()
            key = f'{path}|{st.st_size}|{int(st.st_mtime)}|{args.length}'
        except OSError:
            key = str(path)
        entry = cache.get(key)
        if entry:
            dur, fp = entry['duration'], entry['fp']
        else:
            dur, fp = fingerprint(path, args.length)
            if fp:
                cache[key] = {'duration': dur, 'fp': fp}
        if fp:
            prints.append((path, dur or 0.0, fp))
        else:
            print(f'  ! could not fingerprint: {path}')
        if i % 10 == 0 or i == len(files):
            print(f'  {i}/{len(files)}')

    if not args.no_cache:
        save_cache(cache_root, cache)

    # Pairwise compare (duration pre-filter keeps this fast).
    n = len(prints)
    uf = Union(n)
    pair_sims = {}
    print('Comparing...')
    for i in range(n):
        _, di, fi = prints[i]
        for j in range(i + 1, n):
            _, dj, fj = prints[j]
            if abs(di - dj) > args.duration_slack:
                continue
            sim = compare(fi, fj, args.max_offset)
            if sim >= args.threshold:
                uf.join(i, j)
                pair_sims[(i, j)] = sim

    # Build clusters of size >= 2.
    groups = {}
    for idx in range(n):
        groups.setdefault(uf.find(idx), []).append(idx)
    clusters = [sorted(v) for v in groups.values() if len(v) > 1]

    if not clusters:
        print('\nNo duplicates found. Every track is acoustically unique.')
        return

    to_remove = []
    print(f'\nFound {len(clusters)} duplicate group(s):\n' + '=' * 60)
    prefer = [t for t in args.prefer.split(',') if t]
    for gi, members in enumerate(clusters, 1):
        ranked = sorted(members, key=lambda m: keeper_score(prints[m][0], prefer), reverse=True)
        keep = ranked[0]
        kp, kd, _ = prints[keep]
        kb = bitrate_of(kp) // 1000
        print(f'\nGroup {gi}:')
        print(f'  KEEP   {kp}  [{kd:.0f}s, {kb}kbps]')
        for m in ranked[1:]:
            mp, md, _ = prints[m]
            mb = bitrate_of(mp) // 1000
            sim = pair_sims.get((min(keep, m), max(keep, m)))
            sim_txt = f'{sim*100:.1f}%' if sim is not None else 'linked'
            print(f'  REMOVE {mp}  [{md:.0f}s, {mb}kbps]  (match {sim_txt})')
            to_remove.append(mp)

    print('\n' + '=' * 60)
    print(f'{len(to_remove)} duplicate file(s) across {len(clusters)} group(s).')

    if not args.apply:
        print('\nDRY RUN — nothing changed. Re-run with --apply to quarantine, '
              'or --apply --delete to remove.')
        return

    if args.delete:
        for p in to_remove:
            try:
                p.unlink()
                print(f'  deleted: {p}')
            except OSError as e:
                print(f'  FAILED to delete {p}: {e}')
        print(f'\nDeleted {len(to_remove)} duplicate(s).')
    else:
        quarantine.mkdir(parents=True, exist_ok=True)
        for p in to_remove:
            rel = None
            for r in roots:
                try:
                    rel = p.relative_to(r)
                    break
                except ValueError:
                    continue
            if rel is None:
                rel = Path(p.name)
            dest = quarantine / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_stem(dest.stem + '_dup')
            shutil.move(str(p), str(dest))
            print(f'  moved: {p} -> {dest}')
        print(f'\nMoved {len(to_remove)} duplicate(s) to {quarantine}')


if __name__ == '__main__':
    main()
