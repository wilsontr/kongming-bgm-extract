"""Decoder for the chunked PS-ADPCM BGM containers used in Koei PS2 titles.

The public surface is small and stable:

    kongming_bgm.find_chunks(data)      -> list of ChunkHeader
    kongming_bgm.decode_chunk(...)      -> ChannelPair of int sample lists
    kongming_bgm.analyse(...)           -> verification metrics

See FORMAT.md for the container and codec specification.
"""

from .codec import BLOCK_SIZE, SAMPLES_PER_BLOCK, ChannelDecoder
from .container import (
    ChunkHeader,
    ChannelPair,
    decode_chunk,
    find_chunks,
    looks_like_adpcm,
    read_header,
)
from .diagnostics import analyse

__version__ = "1.0.0"

__all__ = [
    "BLOCK_SIZE",
    "SAMPLES_PER_BLOCK",
    "ChannelDecoder",
    "ChunkHeader",
    "ChannelPair",
    "decode_chunk",
    "find_chunks",
    "looks_like_adpcm",
    "read_header",
    "analyse",
    "__version__",
]
