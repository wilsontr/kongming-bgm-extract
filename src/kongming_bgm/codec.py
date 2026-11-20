"""Sony PS-ADPCM block codec.

A block is 16 bytes and carries 28 samples of a single channel:

    byte 0      control byte: (filter << 4) | shift
    byte 1      reserved, always 0x00
    bytes 2-15  14 bytes = 28 four-bit two's-complement nibbles,
                low nibble of each byte first

Reconstruction, per sample:

    s  = sign_extend_4bit(nibble) << 12
    s  = s >> shift                        # Python >> is arithmetic
    s += (prev1 * C1[filter] + prev2 * C2[filter]) >> 6
    s  = clamp(s, -32768, 32767)
    prev2, prev1 = prev1, s

The predictor state is carried across blocks and is per channel.
"""

BLOCK_SIZE = 16
SAMPLES_PER_BLOCK = 28

#: Control byte holds the filter selector in the high nibble, shift in the low.
FILTER_MAX = 4
SHIFT_MAX = 12

#: Two-tap predictor coefficients for filter selectors 0..4, scaled by 64.
COEFFICIENTS = (
    (0, 0),
    (60, 0),
    (115, -52),
    (98, -55),
    (122, -60),
)

#: Per-shift scaling of a four-bit nibble into the 16-bit sample domain.
SCALE = tuple(
    tuple(((n - 16 if n > 7 else n) << 12) >> shift for n in range(16))
    for shift in range(16)
)


def parse_control(control):
    """Return ``(filter_selector, shift)`` for a block control byte."""
    return (control >> 4) & 0x0F, control & 0x0F


def iter_nibbles(block):
    """Yield the 28 signed nibbles of a block, low nibble of each byte first."""
    for index in range(2, BLOCK_SIZE):
        byte = block[index]
        yield byte & 0x0F
        yield byte >> 4


class ChannelDecoder:
    """Stateful decoder for one channel of a block-interleaved stream."""

    __slots__ = ("_coefficients", "_prev1", "_prev2")

    def __init__(self, coefficients=COEFFICIENTS):
        if len(coefficients) < FILTER_MAX + 1:
            raise ValueError("coefficient table must have at least 5 entries")
        self._coefficients = tuple(tuple(pair) for pair in coefficients)
        self.reset()

    def reset(self):
        """Clear the predictor history, as at the start of a chunk."""
        self._prev1 = 0
        self._prev2 = 0

    @property
    def state(self):
        """The ``(prev1, prev2)`` predictor history."""
        return self._prev1, self._prev2

    def coefficients_for(self, filter_selector):
        """Return the ``(c1, c2)`` coefficients for a filter selector."""
        if filter_selector < len(self._coefficients):
            return self._coefficients[filter_selector]
        return 0, 0

    def decode_nibbles(self, nibbles, filter_selector, shift):
        """Decode a nibble sequence under one filter selector and shift."""
        c1, c2 = self.coefficients_for(filter_selector)
        scale = SCALE[shift]
        prev1, prev2 = self._prev1, self._prev2
        samples = []
        append = samples.append
        for nibble in nibbles:
            value = scale[nibble] + ((prev1 * c1 + prev2 * c2) >> 6)
            if value > 32767:
                value = 32767
            elif value < -32768:
                value = -32768
            append(value)
            prev2, prev1 = prev1, value
        self._prev2, self._prev1 = prev2, prev1
        return samples

    def decode_block(self, block):
        """Decode one 16-byte block into a list of 28 samples.

        Kept as an inlined loop rather than delegating to :meth:`decode_nibbles`
        because this path runs once per block and dominates total runtime.
        """
        if len(block) < BLOCK_SIZE:
            raise ValueError(
                "block truncated: expected %d bytes, got %d" % (BLOCK_SIZE, len(block))
            )
        filter_selector, shift = parse_control(block[0])
        c1, c2 = self.coefficients_for(filter_selector)
        scale = SCALE[shift]
        prev1, prev2 = self._prev1, self._prev2
        samples = []
        append = samples.append
        for index in range(2, BLOCK_SIZE):
            byte = block[index]
            for nibble in (byte & 0x0F, byte >> 4):
                value = scale[nibble] + ((prev1 * c1 + prev2 * c2) >> 6)
                if value > 32767:
                    value = 32767
                elif value < -32768:
                    value = -32768
                append(value)
                prev2, prev1 = prev1, value
        self._prev2, self._prev1 = prev2, prev1
        return samples
