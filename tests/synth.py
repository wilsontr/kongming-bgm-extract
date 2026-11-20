"""Synthetic PS-ADPCM encoder and container builder for tests.

No game data is used anywhere in this repository. These helpers generate valid
PS-ADPCM block streams and container headers from arbitrary PCM, which is enough
to exercise every code path in the decoder.
"""

import struct

from kongming_bgm.codec import (
    BLOCK_SIZE,
    COEFFICIENTS,
    SAMPLES_PER_BLOCK,
    parse_control,
)

SILENT_BLOCK = bytes([0x0C, 0x00] + [0x00] * 14)


def _quantize(residual, step):
    value = int(round(residual / step))
    if value > 7:
        return 7
    if value < -8:
        return -8
    return value


def _clamp16(value):
    if value > 32767:
        return 32767
    if value < -32768:
        return -32768
    return value


def encode_block(samples, state=(0, 0), coefficients=COEFFICIENTS):
    """Encode 28 samples into one block, choosing the best filter and shift.

    Returns ``(block_bytes, (prev1, prev2))``. The search is exhaustive over the
    five filter selectors and thirteen shift values, scored by squared error
    against the values the decoder will actually reconstruct.
    """
    if len(samples) != SAMPLES_PER_BLOCK:
        raise ValueError("need exactly %d samples" % SAMPLES_PER_BLOCK)
    best = None
    for filter_selector in range(5):
        c1, c2 = coefficients[filter_selector]
        for shift in range(13):
            step = 1 << (12 - shift)
            prev1, prev2 = state
            error = 0
            nibbles = []
            for sample in samples:
                predicted = (prev1 * c1 + prev2 * c2) >> 6
                residual = sample - predicted
                nibble = _quantize(residual, step)
                reconstructed = _clamp16(predicted + nibble * step)
                delta = sample - reconstructed
                error += delta * delta
                nibbles.append(nibble)
                prev2, prev1 = prev1, reconstructed
            if best is None or error < best[0]:
                best = (error, filter_selector, shift, nibbles, (prev1, prev2))
    _, filter_selector, shift, nibbles, end_state = best
    payload = bytearray(14)
    for index in range(14):
        low = nibbles[2 * index] & 0x0F
        high = nibbles[2 * index + 1] & 0x0F
        payload[index] = low | (high << 4)
    block = bytes([(filter_selector << 4) | shift, 0x00]) + bytes(payload)
    return block, end_state


def encode_channel(samples):
    """Encode a PCM sequence into a block stream, padding the tail with zeros."""
    data = bytearray()
    state = (0, 0)
    for start in range(0, len(samples), SAMPLES_PER_BLOCK):
        chunk = list(samples[start : start + SAMPLES_PER_BLOCK])
        chunk += [0] * (SAMPLES_PER_BLOCK - len(chunk))
        block, state = encode_block(chunk, state)
        data += block
    if not data:
        data += SILENT_BLOCK
    return bytes(data)


def block_count(stream):
    return len(stream) // BLOCK_SIZE


def interleave(left_stream, right_stream):
    """Interleave two block streams block by block: L, R, L, R, ..."""
    left_blocks = block_count(left_stream)
    right_blocks = block_count(right_stream)
    total = max(left_blocks, right_blocks)
    out = bytearray()
    for index in range(total):
        if index < left_blocks:
            out += left_stream[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        else:
            out += SILENT_BLOCK
        if index < right_blocks:
            out += right_stream[index * BLOCK_SIZE : (index + 1) * BLOCK_SIZE]
        else:
            out += SILENT_BLOCK
    return bytes(out)


def make_header(pad_size=2048, sample_rate=32000, channels=2, bit_depth=16, blocks=1, version=1):
    """Build a 32-byte chunk core header."""
    fields = struct.pack("<6I", pad_size, sample_rate, channels, bit_depth, blocks, version)
    return fields + b"\x00" * (32 - len(fields))


def build_chunk(payload_blocks, pad_size=2048, sample_rate=32000, channels=2, blocks=None):
    """Build one chunk: core header, zero padding, then block data."""
    if blocks is None:
        blocks = block_count(payload_blocks) // (2 if channels > 1 else 1)
    return (
        make_header(pad_size, sample_rate, channels, 16, blocks)
        + b"\x00" * pad_size
        + payload_blocks
    )


def build_container(tracks, sample_rate=32000, pad_size=2048, channels=2):
    """Build a multi-chunk container from ``tracks``.

    Each entry is either a sequence of samples (mono) or a ``(left, right)``
    pair.
    """
    out = bytearray()
    offsets = []
    for track in tracks:
        offsets.append(len(out))
        if channels == 1:
            payload = encode_channel(track)
            blocks = block_count(payload)
        else:
            left, right = track
            payload = interleave(encode_channel(left), encode_channel(right))
            blocks = block_count(payload) // 2
        out += build_chunk(payload, pad_size=pad_size, sample_rate=sample_rate,
                           channels=channels, blocks=blocks)
    return bytes(out), offsets


def sine(frames, frequency, sample_rate=32000, amplitude=8000):
    import math

    return [int(amplitude * math.sin(2.0 * math.pi * frequency * i / sample_rate)) for i in range(frames)]


def handcrafted_block(control, nibbles):
    """Build a block from an explicit control byte and 28 signed nibbles."""
    if len(nibbles) != SAMPLES_PER_BLOCK:
        raise ValueError("need %d nibbles" % SAMPLES_PER_BLOCK)
    payload = bytearray(14)
    for index in range(14):
        payload[index] = (nibbles[2 * index] & 0x0F) | ((nibbles[2 * index + 1] & 0x0F) << 4)
    return bytes([control, 0x00]) + bytes(payload)
