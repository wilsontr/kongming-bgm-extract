"""Container layer: chunk discovery, header parsing, stream assembly.

A file holds a sequence of chunks. Each chunk is:

    +0x000  32 bytes   core header, six little-endian u32
    +0x020  pad        zero padding, length given by the first header field
    +0x020+pad         PS-ADPCM blocks, 16 bytes each

Core header fields:

    [0] pad length following the 32-byte core header   (typically 2048)
    [1] sample rate                                    (32000 or 44100)
    [2] channel count                                  (2)
    [3] nominal bit depth                              (16)
    [4] chunk length in blocks PER CHANNEL             (== audio_blocks / 2)
    [5] version                                        (1)

Chunks are located by scanning for ``00 08 00 00`` followed by a plausible
sample rate and a core header that survives validation. The scan is independent
of file name, so it works equally on a standalone ``*BGM.ADX`` and on a member of
a larger archive such as the ``LINK`` containers that RTK X uses.
"""

import re
import struct
from array import array

from .codec import BLOCK_SIZE, ChannelDecoder, iter_nibbles, parse_control

CORE_HEADER_SIZE = 32
DEFAULT_PAD = 2048

#: Anchor patterns used to locate core headers, paired with the byte offset of
#: the header relative to each match. No single anchor covers every field, so
#: three are used and their candidates merged:
#:
#:   1. channels 2, bit depth 16      -> any pad size, any rate
#:   2. pad size 2048                 -> any channel count, any rate
#:   3. bit depth 16 followed by a chunk length and version 1
#:                                    -> any pad size, any channel count, any rate
ANCHORS = (
    (re.compile(rb"\x02\x00\x00\x00\x10\x00\x00\x00", re.S), -8),
    (re.compile(rb"\x00\x08\x00\x00", re.S), 0),
    (re.compile(rb"\x10\x00\x00\x00.{4}\x01\x00\x00\x00", re.S), -12),
)

MIN_SAMPLE_RATE = 8000
MAX_SAMPLE_RATE = 192000
MAX_PAD = 1 << 20
MAX_CHANNELS = 8
VALID_BIT_DEPTHS = (0, 8, 16, 24, 32)

#: Blocks sampled when validating that a candidate chunk really is ADPCM.
VALIDATION_BLOCKS = 64

INTERLEAVE_MODES = ("block", "mono", "pair")


class ChunkHeader:
    """The six little-endian u32 fields of a chunk core header."""

    __slots__ = (
        "offset",
        "pad_size",
        "sample_rate",
        "channels",
        "bit_depth",
        "blocks_per_channel",
        "version",
    )

    def __init__(
        self, offset, pad_size, sample_rate, channels, bit_depth, blocks_per_channel, version
    ):
        self.offset = offset
        self.pad_size = pad_size
        self.sample_rate = sample_rate
        self.channels = channels
        self.bit_depth = bit_depth
        self.blocks_per_channel = blocks_per_channel
        self.version = version

    @property
    def audio_offset(self):
        """Byte offset of the first ADPCM block."""
        return self.offset + CORE_HEADER_SIZE + self.pad_size

    @property
    def audio_blocks(self):
        """Stated length of the chunk in blocks, both channels combined."""
        return self.blocks_per_channel * 2

    def __repr__(self):
        return (
            "ChunkHeader(offset=0x%X, rate=%d, channels=%d, blocks_per_channel=%d)"
            % (self.offset, self.sample_rate, self.channels, self.blocks_per_channel)
        )


class ChannelPair:
    """Decoded audio for one chunk."""

    __slots__ = ("sample_rate", "left", "right")

    def __init__(self, sample_rate, left, right=None):
        self.sample_rate = sample_rate
        self.left = left
        self.right = right

    @property
    def frames(self):
        return len(self.left)

    @property
    def is_stereo(self):
        return self.right is not None

    def interleaved(self, pad=0):
        """Return the samples interleaved for writing, optionally padded."""
        if self.right is None:
            return array("h", self.left)
        out = array("h")
        append = out.append
        for index in range(len(self.left)):
            append(self.left[index])
            append(self.right[index])
        return out


def read_header(data, offset):
    """Parse the core header at ``offset``.

    Raises :class:`ValueError` if a field is outside the accepted range, which is
    how false positives are rejected.
    """
    if offset + CORE_HEADER_SIZE > len(data):
        raise ValueError("core header truncated at 0x%X" % offset)
    fields = struct.unpack_from("<6I", data, offset)
    pad_size, sample_rate, channels, bit_depth, blocks, version = fields
    if not MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE:
        raise ValueError("implausible sample rate %d at 0x%X" % (sample_rate, offset))
    if not 0 < pad_size <= MAX_PAD:
        raise ValueError("implausible pad size %d at 0x%X" % (pad_size, offset))
    if not 1 <= channels <= MAX_CHANNELS:
        raise ValueError("implausible channel count %d at 0x%X" % (channels, offset))
    if bit_depth not in VALID_BIT_DEPTHS:
        raise ValueError("implausible bit depth %d at 0x%X" % (bit_depth, offset))
    if version > 0xFFFF:
        raise ValueError("implausible version %d at 0x%X" % (version, offset))
    if blocks == 0:
        raise ValueError("zero-length chunk at 0x%X" % offset)
    return ChunkHeader(offset, pad_size, sample_rate, channels, bit_depth, blocks, version)


def looks_like_adpcm(data, start, blocks=VALIDATION_BLOCKS):
    """Return the fraction of sampled blocks that look like PS-ADPCM.

    A conforming block has a zero reserved byte and a filter selector of at most
    4 in the high nibble of the control byte. On real data this is 1.0; an
    unrelated byte stream scores far below that.
    """
    if start < 0 or start >= len(data):
        return 0.0
    available = (len(data) - start) // BLOCK_SIZE
    count = min(blocks, available)
    if count <= 0:
        return 0.0
    good = 0
    for index in range(count):
        base = start + index * BLOCK_SIZE
        if data[base + 1] != 0:
            continue
        filter_selector, _ = parse_control(data[base])
        if filter_selector > 4:
            continue
        good += 1
    return good / count


def validation_score(data, header):
    """ADPCM conformity of a chunk, sampling only within its stated length."""
    return looks_like_adpcm(
        data, header.audio_offset, min(VALIDATION_BLOCKS, header.audio_blocks)
    )


def find_chunks(data, validate=True):
    """Return every plausible chunk header in ``data``, in file order.

    ``data`` may be bytes or an mmap. Validation samples only as many blocks as
    the header claims, so a short chunk followed immediately by another header
    is not rejected for what lies past its own end.
    """
    length = len(data)
    candidates = {}
    for pattern, delta in ANCHORS:
        for match in pattern.finditer(data):
            offset = match.start() + delta
            if offset < 0 or offset in candidates:
                continue
            try:
                header = read_header(data, offset)
            except ValueError:
                continue
            if header.audio_offset >= length:
                continue
            candidates[offset] = header
    chunks = [candidates[offset] for offset in sorted(candidates)]
    if validate:
        chunks = [header for header in chunks if validation_score(data, header) >= 1.0]
    return _drop_overlaps(chunks)


def _drop_overlaps(chunks):
    kept = []
    for header in chunks:
        if kept and header.offset < kept[-1].audio_offset:
            continue
        kept.append(header)
    return kept


def audio_end(header, limit):
    """Return the byte offset where a chunk's audio stops.

    The header states the length in blocks per channel. That value agrees with
    the distance to the next chunk to within a fraction of a percent, so the
    stated length is used and then clamped to ``limit`` (the next chunk, or end
    of file).
    """
    natural = header.audio_offset + header.audio_blocks * BLOCK_SIZE
    end = min(natural, limit)
    end -= (end - header.audio_offset) % BLOCK_SIZE
    return end


def audio_end_for_chunks(chunks, data_length, index):
    """Return the audio end offset for ``chunks[index]``."""
    limit = chunks[index + 1].offset if index + 1 < len(chunks) else data_length
    return audio_end(chunks[index], limit)


def decode_chunk(data, header, end, interleave="block"):
    """Decode the blocks in ``[header.audio_offset, end)``.

    ``interleave`` selects how blocks map onto channels:

    ``block``  consecutive blocks alternate left, right (verified on all
               titles tested so far). The default.
    ``mono``   every block belongs to a single channel.
    ``pair``   nibbles alternate left, right within each block.
    """
    if interleave not in INTERLEAVE_MODES:
        raise ValueError("unknown interleave mode %r" % (interleave,))
    start = header.audio_offset
    if end < start:
        raise ValueError("empty range")
    if interleave == "mono" or header.channels == 1:
        decoder = ChannelDecoder()
        left = array("h")
        for base in range(start, end, BLOCK_SIZE):
            left.extend(decoder.decode_block(data[base : base + BLOCK_SIZE]))
        return ChannelPair(header.sample_rate, left, None)

    left_decoder = ChannelDecoder()
    right_decoder = ChannelDecoder()
    left = array("h")
    right = array("h")

    if interleave == "block":
        for index, base in enumerate(range(start, end, BLOCK_SIZE)):
            block = data[base : base + BLOCK_SIZE]
            if index & 1:
                right.extend(right_decoder.decode_block(block))
            else:
                left.extend(left_decoder.decode_block(block))
    else:  # pair
        for base in range(start, end, BLOCK_SIZE):
            block = data[base : base + BLOCK_SIZE]
            filter_selector, shift = parse_control(block[0])
            nibbles = list(iter_nibbles(block))
            left.extend(left_decoder.decode_nibbles(nibbles[0::2], filter_selector, shift))
            right.extend(right_decoder.decode_nibbles(nibbles[1::2], filter_selector, shift))

    frames = min(len(left), len(right))
    return ChannelPair(header.sample_rate, left[:frames], right[:frames])
