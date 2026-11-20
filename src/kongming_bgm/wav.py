"""WAV output helpers."""

import wave
from array import array

#: Frames whose absolute value is at or below this are treated as silence.
SILENCE_THRESHOLD = 16

#: Margin kept either side when trimming, in milliseconds.
TRIM_PAD_MS = 250


def _first_active(pair, threshold):
    left = pair.left
    right = pair.right
    frames = len(left)
    for index in range(frames):
        if right is None:
            if -threshold <= left[index] <= threshold:
                continue
            return index
        if -threshold <= left[index] <= threshold and -threshold <= right[index] <= threshold:
            continue
        return index
    return frames


def _last_active(pair, threshold):
    left = pair.left
    right = pair.right
    for index in range(len(left) - 1, -1, -1):
        if right is None:
            if -threshold <= left[index] <= threshold:
                continue
            return index
        if -threshold <= left[index] <= threshold and -threshold <= right[index] <= threshold:
            continue
        return index
    return -1


def trim_range(pair, threshold=SILENCE_THRESHOLD, pad_ms=TRIM_PAD_MS):
    """Return ``(start, end)`` frame indices bounding the non-silent content."""
    frames = len(pair.left)
    first = _first_active(pair, threshold)
    if first >= frames:
        return 0, frames
    last = _last_active(pair, threshold)
    pad = int(pair.sample_rate * pad_ms / 1000) if pair.sample_rate else 0
    return max(0, first - pad), min(frames, last + 1 + pad)


def write_wav(path, pair, trim=False, threshold=SILENCE_THRESHOLD, pad_ms=TRIM_PAD_MS):
    """Write ``pair`` as 16-bit PCM. Returns a dict describing what was written."""
    start, end = (0, len(pair.left))
    if trim:
        start, end = trim_range(pair, threshold, pad_ms)
    channels = 2 if pair.is_stereo else 1
    frames = end - start

    if channels == 2:
        interleaved = array("h")
        append = interleaved.append
        left = pair.left
        right = pair.right
        for index in range(start, end):
            append(left[index])
            append(right[index])
    else:
        interleaved = pair.left[start:end]

    with wave.open(path, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(pair.sample_rate)
        handle.writeframes(interleaved.tobytes())

    return {
        "path": path,
        "samples": frames,
        "channels": channels,
        "sample_rate": pair.sample_rate,
        "trimmed_lead_frames": start,
        "trimmed_tail_frames": len(pair.left) - end,
    }
