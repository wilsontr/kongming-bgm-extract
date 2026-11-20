"""Dependency-free verification metrics.

These exist to make a wrong decode visible instead of merely audible. A correct
PS-ADPCM decode of music lands in a narrow band: roughness well below 0.4,
spectral flatness below 0.05, most energy below 2 kHz. A mono fold-down of an
interleaved stereo stream, a wrong predictor, or a wrong block offset all push
those numbers out.

Metrics are computed over a sampled window rather than the whole track so that
reporting stays cheap on long files.
"""

import math

DEFAULT_WINDOW = 400_000
FRAME_SIZE = 512
MAX_FRAMES = 8


def _window(length, window):
    if length <= window:
        return 0, length
    start = (length - window) // 4
    return start, start + window


def _fft_magnitudes(real, imag, twiddle_re, twiddle_im):
    n = len(real)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            real[i], real[j] = real[j], real[i]
            imag[i], imag[j] = imag[j], imag[i]
    size = 2
    while size <= n:
        half = size >> 1
        step = n // size
        for start in range(0, n, size):
            k = 0
            for offset in range(start, start + half):
                wr = twiddle_re[k]
                wi = twiddle_im[k]
                ir = real[offset + half]
                ii = imag[offset + half]
                tr = ir * wr - ii * wi
                ti = ir * wi + ii * wr
                real[offset + half] = real[offset] - tr
                imag[offset + half] = imag[offset] - ti
                real[offset] += tr
                imag[offset] += ti
                k += step
        size <<= 1
    return [math.hypot(real[k], imag[k]) for k in range(n // 2 + 1)]


def _twiddles(n):
    return (
        [math.cos(-2.0 * math.pi * k / n) for k in range(n // 2)],
        [math.sin(-2.0 * math.pi * k / n) for k in range(n // 2)],
    )


def _spectral(samples, sample_rate, frame_size=FRAME_SIZE, max_frames=MAX_FRAMES):
    """Return (flatness, low_band_ratio, centroid_hz) or (None, None, None)."""
    if len(samples) < frame_size:
        return None, None, None
    twiddle_re, twiddle_im = _twiddles(frame_size)
    usable = len(samples) - frame_size
    stride = max(1, usable // max(1, max_frames)) if max_frames > 1 else usable
    powers = None
    frames = 0
    for index in range(0, max(1, max_frames)):
        start = min(usable, index * stride)
        frame = samples[start : start + frame_size]
        mean = sum(frame) / frame_size
        windowed = [
            0.5 - 0.5 * math.cos(2.0 * math.pi * i / frame_size) for i in range(frame_size)
        ]
        real = [(frame[i] - mean) * windowed[i] for i in range(frame_size)]
        imag = [0.0] * frame_size
        magnitudes = _fft_magnitudes(real, imag, twiddle_re, twiddle_im)
        power = [(magnitude * magnitude) for magnitude in magnitudes]
        power[0] = 0.0
        if powers is None:
            powers = power
        else:
            for k in range(len(power)):
                powers[k] += power[k]
        frames += 1
    if not frames:
        return None, None, None
    bins = len(powers)
    total = sum(powers[1:])
    if total <= 0:
        return None, None, None
    low_cutoff = min(bins, max(1, int(2000 * frame_size / sample_rate)))
    low = sum(powers[1:low_cutoff])
    positive = [value for value in powers[1:] if value > 0]
    if len(positive) < 2:
        return None, None, None
    log_mean = sum(math.log(value) for value in positive) / len(positive)
    flatness = math.exp(log_mean) / (sum(positive) / len(positive))
    centroid = sum(k * powers[k] for k in range(1, bins)) / total
    centroid_hz = centroid * sample_rate / frame_size
    return flatness, low / total, centroid_hz


def analyse(pair, window=DEFAULT_WINDOW):
    """Return a dict of verification metrics for a decoded :class:`ChannelPair`."""
    left = pair.left
    frames = len(left)
    start, end = _window(frames, window)
    segment = left[start:end]
    count = len(segment)

    result = {
        "frames": frames,
        "seconds": frames / pair.sample_rate if pair.sample_rate else 0.0,
        "sample_rate": pair.sample_rate,
        "channels": 2 if pair.is_stereo else 1,
        "window_frames": count,
    }
    if count == 0:
        return result

    peak = 0
    total = 0
    clipped = 0
    previous = None
    deltas = 0
    crossings = 0
    for value in segment:
        magnitude = value if value >= 0 else -value
        if magnitude > peak:
            peak = magnitude
        total += value * value
        if magnitude >= 32767:
            clipped += 1
        if previous is not None:
            delta = value - previous
            if delta < 0:
                delta = -delta
            deltas += delta
            if (value >= 0) != (previous >= 0):
                crossings += 1
        previous = value

    rms = math.sqrt(total / count)
    result["peak"] = peak
    result["rms"] = rms
    result["dc_offset"] = sum(segment) / count
    result["clipped_samples"] = clipped
    result["roughness"] = (deltas / (count - 1)) / rms if count > 1 and rms else None
    result["zero_crossing_rate"] = (crossings / (count - 1)) if count > 1 else None

    if pair.is_stereo:
        right = pair.right[start:end]
        result["correlation_lr"] = _pearson(segment, right)

    flatness, low_ratio, centroid = _spectral(segment, pair.sample_rate)
    result["spectral_flatness"] = flatness
    result["low_band_ratio"] = low_ratio
    result["centroid_hz"] = centroid
    return result


def _pearson(a, b):
    n = min(len(a), len(b))
    if n < 2:
        return None
    mean_a = sum(a[:n]) / n
    mean_b = sum(b[:n]) / n
    num = 0.0
    den_a = 0.0
    den_b = 0.0
    for index in range(n):
        da = a[index] - mean_a
        db = b[index] - mean_b
        num += da * db
        den_a += da * da
        den_b += db * db
    if den_a <= 0 or den_b <= 0:
        return None
    return num / math.sqrt(den_a * den_b)
