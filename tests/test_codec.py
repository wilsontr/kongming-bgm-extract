"""Codec tests.

Expected values are computed by hand from the recurrence in FORMAT.md, not by
calling the decoder a second time.
"""

import math

import pytest

from kongming_bgm.codec import (
    BLOCK_SIZE,
    SAMPLES_PER_BLOCK,
    ChannelDecoder,
    iter_nibbles,
    parse_control,
)

import synth


def decode_bytes(data):
    decoder = ChannelDecoder()
    samples = []
    for base in range(0, len(data), BLOCK_SIZE):
        samples.extend(decoder.decode_block(data[base : base + BLOCK_SIZE]))
    return samples


def test_control_byte_fields():
    assert parse_control(0x2C) == (2, 12)
    assert parse_control(0x00) == (0, 0)
    assert parse_control(0x4C) == (4, 12)


def test_block_size_and_sample_count():
    block = synth.handcrafted_block(0x0C, [0] * SAMPLES_PER_BLOCK)
    assert len(block) == BLOCK_SIZE
    assert len(list(iter_nibbles(block))) == SAMPLES_PER_BLOCK


def test_filter0_shift12_returns_nibbles_verbatim():
    nibbles = [0, 1, 2, 3, 4, 5, 6, 7, -8, -7, -6, -5, -4, -3, -2, -1] + [0] * 12
    block = synth.handcrafted_block(0x0C, nibbles)  # filter 0, shift 12
    assert decode_bytes(block) == nibbles


def test_nibble_order_is_low_first():
    # One byte 0x21 packs nibbles 1 and 2; low-first order yields [1, 2].
    block = bytes([0x0C, 0x00, 0x21]) + bytes(13)
    samples = decode_bytes(block)
    assert samples[0] == 1
    assert samples[1] == 2


def test_filter1_geometric_decay_matches_hand_computation():
    # filter 1 => (c1, c2) = (60, 0); shift 0 => scale = nibble << 12.
    # First nibble 1, remainder 0, state starts at zero:
    #   s0 = 4096
    #   s1 = 0 + (4096*60)>>6 = 3840
    #   s2 = 0 + (3840*60)>>6 = 3600
    #   s3 = 0 + (3600*60)>>6 = 3375
    #   s4 = 0 + (3375*60)>>6 = 202500>>6 = 3164   (floor, not 3164.06)
    block = bytes([0x10, 0x00, 0x01]) + bytes(13)
    samples = decode_bytes(block)
    assert samples[:5] == [4096, 3840, 3600, 3375, 3164]


def test_predictor_state_persists_across_blocks():
    # A single 1 nibble at shift 0 in block 1 must still decay through block 2.
    first = bytes([0x10, 0x00, 0x01]) + bytes(13)
    second = bytes([0x10, 0x00]) + bytes(14)
    samples = decode_bytes(first + second)
    # Callable continuity: block 2's first sample continues the decay from
    # block 1's last sample rather than restarting from zero.
    last = samples[SAMPLES_PER_BLOCK - 1]
    assert last > 0
    assert samples[SAMPLES_PER_BLOCK] == (last * 60) >> 6


def test_reset_clears_history():
    decoder = ChannelDecoder()
    decoder.decode_block(bytes([0x10, 0x00, 0x01]) + bytes(13))
    assert decoder.state != (0, 0)
    decoder.reset()
    assert decoder.state == (0, 0)
    assert decoder.decode_block(bytes([0x0C, 0x00, 0x01]) + bytes(13))[0] == 1


def test_output_is_clamped_to_16_bits():
    nibbles = [7] * SAMPLES_PER_BLOCK
    block = synth.handcrafted_block(0x40, nibbles)  # filter 4, shift 0
    samples = decode_bytes(block)
    assert max(samples) == 32767
    assert min(samples) >= -32768


def test_truncated_block_raises():
    decoder = ChannelDecoder()
    with pytest.raises(ValueError):
        decoder.decode_block(b"\x00" * 8)


def test_roundtrip_error_is_small():
    source = synth.sine(28 * 40, frequency=220.0, amplitude=9000)
    stream = synth.encode_channel(source)
    decoded = decode_bytes(stream)[: len(source)]
    assert len(decoded) == len(source)
    error = [decoded[i] - source[i] for i in range(len(source))]
    rms_signal = math.sqrt(sum(value * value for value in source) / len(source))
    rms_error = math.sqrt(sum(value * value for value in error) / len(error))
    assert rms_error < 0.05 * rms_signal
    assert max(abs(value) for value in error) < 1200
