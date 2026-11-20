"""Container tests: discovery, header parsing, extent calculation, assembly."""

import math
import struct

import pytest

from kongming_bgm.codec import SAMPLES_PER_BLOCK
from kongming_bgm.container import (
    CORE_HEADER_SIZE,
    audio_end,
    audio_end_for_chunks,
    decode_chunk,
    find_chunks,
    looks_like_adpcm,
    read_header,
)

import synth


def two_track_container(rate=32000):
    left_a = synth.sine(28 * 12, 220.0, rate, 9000)
    right_a = synth.sine(28 * 12, 700.0, rate, 9000)
    left_b = synth.sine(28 * 8, 440.0, rate, 6000)
    right_b = synth.sine(28 * 8, 300.0, rate, 6000)
    return synth.build_container([(left_a, right_a), (left_b, right_b)], sample_rate=rate)


def test_finds_both_chunks():
    data, offsets = two_track_container()
    chunks = find_chunks(data)
    assert len(chunks) == 2
    assert [header.offset for header in chunks] == offsets


def test_header_fields_round_trip():
    data, offsets = two_track_container(rate=44100)
    header = read_header(data, offsets[0])
    assert header.pad_size == 2048
    assert header.sample_rate == 44100
    assert header.channels == 2
    assert header.bit_depth == 16
    assert header.version == 1
    assert header.blocks_per_channel == 12
    assert header.audio_offset == offsets[0] + CORE_HEADER_SIZE + 2048


def test_read_header_rejects_implausible_rate():
    block = synth.make_header(sample_rate=17, blocks=4)
    with pytest.raises(ValueError):
        read_header(block, 0)


def test_read_header_rejects_zero_length_chunk():
    block = synth.make_header(blocks=0)
    with pytest.raises(ValueError):
        read_header(block, 0)


def test_rejects_payload_that_is_not_adpcm():
    header = synth.make_header(sample_rate=32000, blocks=8)
    payload = b"\xff" * (16 * 16)
    data = header + b"\x00" * 2048 + payload
    assert looks_like_adpcm(data, CORE_HEADER_SIZE + 2048) < 1.0
    assert find_chunks(data) == []
    # The header alone still parses; only validation drops it.
    assert len(find_chunks(data, validate=False)) == 1


def test_rejects_implausible_scan_candidate():
    data = b"\x00\x08\x00\x00" + struct.pack("<5I", 0, 2, 16, 4, 1) + b"\x00" * 12
    data += b"\x00" * 4096
    assert find_chunks(data, validate=False) == []


def test_audio_end_uses_stated_length():
    data, offsets = two_track_container()
    header = read_header(data, offsets[0])
    limit = len(data)
    end = audio_end(header, limit)
    assert end == header.audio_offset + header.blocks_per_channel * 2 * 16


def test_audio_end_is_clamped_by_next_chunk():
    data, offsets = two_track_container()
    chunks = find_chunks(data)
    end = audio_end_for_chunks(chunks, len(data), 0)
    assert end == chunks[1].offset


def test_decode_recovers_both_channels():
    rate = 32000
    left = synth.sine(28 * 12, 220.0, rate, 9000)
    right = synth.sine(28 * 12, 700.0, rate, 9000)
    data, offsets = synth.build_container([(left, right)], sample_rate=rate)
    chunks = find_chunks(data)
    assert len(chunks) == 1
    pair = decode_chunk(data, chunks[0], audio_end_for_chunks(chunks, len(data), 0))
    assert pair.is_stereo
    assert pair.sample_rate == rate
    assert pair.frames == len(left)

    for decoded, source in ((pair.left, left), (pair.right, right)):
        error = [decoded[i] - source[i] for i in range(len(source))]
        rms_error = math.sqrt(sum(value * value for value in error) / len(error))
        assert rms_error < 0.05 * 9000

    # Distinct tones must not look like a mono fold-down.
    correlation = _correlation(pair.left, pair.right)
    assert abs(correlation) < 0.5


def test_decode_mono_mode():
    samples = synth.sine(28 * 6, 250.0, 32000, 7000)
    data, offsets = synth.build_container([samples], sample_rate=32000, channels=1)
    chunks = find_chunks(data)
    assert len(chunks) == 1
    pair = decode_chunk(data, chunks[0], audio_end_for_chunks(chunks, len(data), 0), interleave="mono")
    assert not pair.is_stereo
    assert pair.frames == len(samples)
    error = [pair.left[i] - samples[i] for i in range(len(samples))]
    rms_error = math.sqrt(sum(value * value for value in error) / len(error))
    assert rms_error < 0.05 * 7000


def test_decode_pair_interleave_returns_stereo():
    left = synth.sine(28 * 4, 200.0, 32000, 5000)
    right = synth.sine(28 * 4, 900.0, 32000, 5000)
    data, offsets = synth.build_container([(left, right)], sample_rate=32000)
    chunks = find_chunks(data)
    pair = decode_chunk(
        data, chunks[0], audio_end_for_chunks(chunks, len(data), 0), interleave="pair"
    )
    assert pair.is_stereo
    assert pair.frames == len(left)


def test_unknown_interleave_rejected():
    data, offsets = two_track_container()
    chunks = find_chunks(data)
    with pytest.raises(ValueError):
        decode_chunk(data, chunks[0], chunks[1].offset, interleave="sideways")


def _correlation(a, b):
    n = min(len(a), len(b))
    mean_a = sum(a[:n]) / n
    mean_b = sum(b[:n]) / n
    num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    den_a = math.sqrt(sum((a[i] - mean_a) ** 2 for i in range(n)))
    den_b = math.sqrt(sum((b[i] - mean_b) ** 2 for i in range(n)))
    return num / (den_a * den_b) if den_a and den_b else 0.0


def test_custom_pad_size_is_honoured():
    samples = synth.sine(28 * 3, 300.0, 32000, 5000)
    payload = synth.encode_channel(samples)
    data = (
        synth.make_header(pad_size=512, sample_rate=32000, channels=1, blocks=3)
        + b"\x00" * 512
        + payload
    )
    chunks = find_chunks(data)
    assert len(chunks) == 1
    assert chunks[0].pad_size == 512
    assert chunks[0].audio_offset == CORE_HEADER_SIZE + 512
    pair = decode_chunk(data, chunks[0], len(data), interleave="mono")
    assert pair.frames == len(samples)
