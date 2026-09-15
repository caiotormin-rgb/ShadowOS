#!/usr/bin/env python3
"""Split a long 16 kHz mono S16LE WAV into chunks for sequential transcription.

Used by tools/media-transcription/bin/transcribe-audio-local for notes longer than TRANSCRIBE_LONG_S:

    python3 -I transcribe_audio_chunks.py <input.wav> <out-dir> <max-chunk-ms>

Writes out-dir/chunk000.wav, chunk001.wav, ... and prints one line per chunk:
"<path> <start_ms> <end_ms>". Every chunk is at most max-chunk-ms long.

whisper writes its transcript only when a run finishes, so one pass over a
4-minute note that hits the time budget returns nothing. Transcribing chunks
in order keeps every finished chunk. Each cut is placed at the quietest 100 ms
window in the last 5 s before the limit, so words are rarely split.
"""
import array
import os
import sys
import wave

RATE = 16000
SEARCH_MS = 5000
WINDOW_MS = 100


def quietest_cut(samples, limit, search, window):
    """Sample index to cut at: centre of the quietest window in [limit-search, limit]."""
    best, best_energy = limit, None
    pos = max(0, limit - search)
    step = window // 2
    while pos + window <= limit:
        energy = sum(abs(s) for s in samples[pos:pos + window])
        if best_energy is None or energy < best_energy:
            best_energy, best = energy, pos + step
        pos += step
    return best


def split(wav_path, out_dir, max_chunk_ms):
    with wave.open(wav_path, 'rb') as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, RATE):
            raise ValueError('expected 16 kHz mono 16-bit WAV')
        frames = w.readframes(w.getnframes())
    samples = array.array('h')
    samples.frombytes(frames[:len(frames) - len(frames) % 2])
    if sys.byteorder == 'big':
        samples.byteswap()
    total = len(samples)
    chunk = max_chunk_ms * RATE // 1000
    search = min(SEARCH_MS, max_chunk_ms // 2) * RATE // 1000
    window = WINDOW_MS * RATE // 1000
    os.makedirs(out_dir, exist_ok=True)
    start, index, lines = 0, 0, []
    while start < total:
        limit = start + chunk
        end = total if limit >= total else quietest_cut(samples, limit, search, window)
        if end <= start:
            end = min(total, limit)
        path = os.path.join(out_dir, f'chunk{index:03d}.wav')
        with wave.open(path, 'wb') as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(frames[start * 2:end * 2])
        lines.append(f'{path} {start * 1000 // RATE} {end * 1000 // RATE}')
        start, index = end, index + 1
    return lines


def main(argv):
    if len(argv) != 4 or not argv[3].isdigit() or int(argv[3]) < 1000:
        sys.stderr.write('usage: transcribe_audio_chunks.py <input.wav> <out-dir> <max-chunk-ms >= 1000>\n')
        return 2
    try:
        lines = split(argv[1], argv[2], int(argv[3]))
    except (OSError, ValueError, wave.Error) as e:
        sys.stderr.write(f'transcribe_audio_chunks: {e}\n')
        return 1
    sys.stdout.write('\n'.join(lines) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
