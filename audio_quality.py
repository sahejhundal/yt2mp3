"""Estimate REAL audio quality of (lossy) files, not just the tagged bitrate.

Two files can both claim 320 kbps yet differ a lot: a file transcoded from a
low-quality source gets low-pass filtered, so its audio has no energy above
~16 kHz, while a genuine high-quality encode reaches ~19-20 kHz. We measure:

  * cutoff_khz : highest frequency that still carries real energy (the single
                 best tell for lossy-source / transcode quality; higher = better)
  * lufs       : integrated loudness (EBU R128). Tells you if one file is just
                 LOUDER rather than better.
  * true_peak  : dBTP; >0 means clipping.
  * hf_ratio   : share of spectral energy above 14 kHz (more air/detail).

Usage:
  python audio_quality.py "a.mp3" "b.mp3" ...
  python audio_quality.py --pair "keep.mp3" "candidate.mp3"
"""
import argparse
import subprocess
import sys

import numpy as np

SR = 44100


def decode_mono(path):
    """Decode a file to mono float32 PCM at SR via ffmpeg."""
    p = subprocess.run(
        ['ffmpeg', '-v', 'error', '-i', str(path),
         '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'],
        capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode('utf-8', 'replace')[:200])
    return np.frombuffer(p.stdout, dtype=np.float32)


def spectrum_db(samples, nfft=8192):
    """Average magnitude spectrum (dB, peak-normalised) over the whole file."""
    if samples.size < nfft:
        samples = np.pad(samples, (0, nfft - samples.size))
    win = np.hanning(nfft)
    step = nfft  # non-overlapping is plenty for an average
    frames = []
    # Skip near-silent frames so leading/trailing silence doesn't skew things.
    for i in range(0, samples.size - nfft, step):
        seg = samples[i:i + nfft]
        if np.sqrt(np.mean(seg * seg)) < 1e-4:
            continue
        frames.append(np.abs(np.fft.rfft(seg * win)))
    if not frames:
        seg = samples[:nfft]
        frames = [np.abs(np.fft.rfft(seg * win))]
    mag = np.mean(frames, axis=0)
    mag /= (mag.max() + 1e-12)
    db = 20 * np.log10(mag + 1e-12)
    freqs = np.fft.rfftfreq(nfft, 1 / SR)
    return freqs, db


def cutoff_khz(freqs, db, floor_db=-70.0, above=-60.0):
    """Highest frequency whose smoothed energy is still above `above` dB.

    Smooth the spectrum, then find the last frequency that clears the
    threshold; this is where the encoder's low-pass wall sits.
    """
    # smooth ~ 200 Hz
    n = max(1, int(200 / (freqs[1] - freqs[0])))
    kernel = np.ones(n) / n
    sm = np.convolve(db, kernel, mode='same')
    sm[sm < floor_db] = floor_db
    idx = np.where(sm > above)[0]
    if idx.size == 0:
        return 0.0
    return freqs[idx[-1]] / 1000.0


def hf_ratio(freqs, db, split_khz=14.0):
    """Energy share above split_khz (linear power)."""
    power = 10 ** (db / 10)
    hi = power[freqs >= split_khz * 1000].sum()
    return float(hi / (power.sum() + 1e-12))


def loudness(path):
    """Integrated LUFS + true peak via ffmpeg ebur128."""
    p = subprocess.run(
        ['ffmpeg', '-v', 'info', '-i', str(path),
         '-af', 'ebur128=peak=true', '-f', 'null', '-'],
        capture_output=True)
    txt = p.stderr.decode('utf-8', 'replace')
    lufs = tpk = None
    # Parse the trailing "Summary:" block.
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith('I:') and 'LUFS' in s:
            try:
                lufs = float(s.split()[1])
            except Exception:
                pass
        if s.startswith('Peak:') and 'dBFS' in s:
            try:
                tpk = float(s.split()[1])
            except Exception:
                pass
    return lufs, tpk


def bitrate_kbps(path):
    p = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'a:0',
         '-show_entries', 'stream=bit_rate:format=bit_rate',
         '-of', 'default=nw=1:nk=1', str(path)], capture_output=True)
    for tok in p.stdout.decode().split():
        if tok.isdigit():
            return int(tok) // 1000
    return None


def analyse(path):
    x = decode_mono(path)
    freqs, db = spectrum_db(x)
    return {
        'path': path,
        'bitrate': bitrate_kbps(path),
        'cutoff_khz': round(cutoff_khz(freqs, db), 1),
        'hf_ratio_pct': round(hf_ratio(freqs, db) * 100, 3),
        'lufs': (lambda l: round(l, 1) if l is not None else None)(loudness(path)[0]),
        'true_peak_db': (lambda t: round(t, 1) if t is not None else None)(loudness(path)[1]),
    }


def verdict(a, b):
    """Which is better on real quality (cutoff first, then HF detail)?"""
    ca, cb = a['cutoff_khz'], b['cutoff_khz']
    if abs(ca - cb) >= 0.8:
        win = a if ca > cb else b
        return f"BETTER QUALITY: {win['path']}  (higher cutoff {max(ca,cb)} vs {min(ca,cb)} kHz)"
    # cutoffs similar -> compare HF detail, note loudness difference
    ha, hb = a['hf_ratio_pct'], b['hf_ratio_pct']
    la, lb = a['lufs'] or -99, b['lufs'] or -99
    loud = 'a' if la > lb else 'b'
    if abs(ha - hb) < 0.05:
        return (f"~EQUAL quality (cutoff {ca} vs {cb} kHz, HF {ha}% vs {hb}%). "
                f"Loudness differs by {round(abs(la-lb),1)} LUFS -> the louder one "
                f"is just louder, not better.")
    win = a if ha > hb else b
    return (f"Slightly better detail: {win['path']} (HF {max(ha,hb)}% vs {min(ha,hb)}%). "
            f"Loudness {round(abs(la-lb),1)} LUFS apart ({'A' if loud=='a' else 'B'} louder).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+')
    ap.add_argument('--pair', action='store_true',
                    help='treat the two files as a duplicate pair and print a verdict')
    args = ap.parse_args()

    results = []
    for f in args.files:
        try:
            r = analyse(f)
        except Exception as e:
            print(f'FAILED {f}: {e}')
            continue
        results.append(r)
        print(f"\n{f}")
        print(f"  tagged bitrate : {r['bitrate']} kbps")
        print(f"  real cutoff    : {r['cutoff_khz']} kHz   (higher = better; "
              f"~20 = true 320, ~16 = transcoded from low quality)")
        print(f"  HF energy >14k : {r['hf_ratio_pct']} %")
        print(f"  loudness       : {r['lufs']} LUFS   true peak {r['true_peak_db']} dBTP")

    if args.pair and len(results) == 2:
        print('\n' + '=' * 60)
        print(verdict(results[0], results[1]))
        print('=' * 60)


if __name__ == '__main__':
    main()
