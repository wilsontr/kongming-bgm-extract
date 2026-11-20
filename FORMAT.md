# Container and codec specification

This document describes the audio container found in several Koei PS2 strategy
titles. It was derived by inspecting discs; no code, data or documentation from
the games was used as input.

All integers are little-endian unless stated otherwise.

## 1. Scope

A BGM file is a sequence of chunks concatenated back to back. Each chunk carries
one track: a 32-byte core header, a block of zero padding, then a stream of
16-byte PS-ADPCM blocks.

The chunks are self-describing. There is no global index and no global header
that covers more than the first chunk, so a decoder locates tracks by scanning.

## 2. Chunk layout

```
+0x000   32 bytes    core header, six u32
+0x020   pad         zero padding; pad length is core header field 0
+0x020+pad           PS-ADPCM blocks, 16 bytes each
```

The audio offset is therefore `chunk_offset + 32 + pad`. For every configuration
tested, `pad` is 2048, giving an audio offset of `chunk_offset + 0x820`.

### 2.1 Core header

| Offset | Type | Field | Observed values |
|---|---|---|---|
| `+0x00` | u32 | padding length following the core header | 2048 |
| `+0x04` | u32 | sample rate in Hz | 32000, 44100 |
| `+0x08` | u32 | channel count | 2 |
| `+0x0C` | u32 | nominal bit depth | 16 |
| `+0x10` | u32 | chunk length in blocks **per channel** | varies |
| `+0x14` | u32 | format version | 1 |
| `+0x18` | 8 bytes | zero | — |

Field 4 counts blocks per channel, not blocks overall. On every chunk examined,
`field4` equals `(audio_bytes / 16) / 2` to within 0.1 %; the small surplus in
the physical span is trailing padding, which is why the stated length is used in
preference to the distance to the next chunk.

Fields 2 and 3 are advisory. Field 2 does describe the real channel layout
(section 4). Field 3 does not describe the encoded signal, which is 4-bit
predictive regardless.

### 2.2 Locating chunks

There is no index, so headers are found by pattern. No single anchor covers all
six fields; the decoder uses three and merges the results.

| Anchor | Implied offset of header | Fields constrained |
|---|---|---|
| `02 00 00 00 10 00 00 00` | match − 8 | channels = 2, bit depth = 16 |
| `00 08 00 00` | match | pad = 2048 |
| `10 00 00 00 ?? ?? ?? ?? 01 00 00 00` | match − 12 | bit depth = 16, version = 1 |

Each candidate is then parsed and rejected unless every field is plausible
(section 2.3), and unless the blocks that follow the padding look like PS-ADPCM
(section 3.1). On the titles tested this yields an exact chunk count with no
false positives.

A limitation follows directly: a title with pad ≠ 2048, channels ≠ 2 and
non-16-bit nominal depth would match no anchor. No such title has been observed.

### 2.3 Field validation

Rejection ranges used by this implementation:

| Field | Accepted |
|---|---|
| pad | `0 < pad <= 0x100000` |
| sample rate | `8000 <= rate <= 192000` |
| channels | `1 <= channels <= 8` |
| bit depth | one of 0, 8, 16, 24, 32 |
| chunk length | `> 0` |
| version | `<= 0xFFFF` |

### 2.4 Chunk extent

Audio occupies `[audio_offset, audio_offset + field4 * 2 * 16)`, clamped to the
next chunk header and to end of file, and truncated to a multiple of 16.

## 3. Codec

Sony PS-ADPCM, the PS1/PS2 SPU block format.

### 3.1 Block layout

```
byte 0      control byte
byte 1      reserved, 0x00 in every block observed
bytes 2-15  14 bytes = 28 four-bit nibbles, low nibble of each byte first
```

A block therefore carries 28 samples of a single channel.

Control byte:

```
filter = (control >> 4) & 0x0F     observed 0..4
shift  =  control       & 0x0F     observed 0..12
```

Block conformity, used to reject false chunk candidates: `byte1 == 0x00` and
`filter <= 4`. On real data this holds for every block except the embedded chunk
headers themselves.

### 3.2 Reconstruction

Per sample, with `prev1` and `prev2` the two previously reconstructed samples of
the same channel:

```
s  = sign_extend_4bit(nibble) << 12
s  = s >> shift                      # arithmetic shift, rounds toward -inf
s += (prev1 * C1[filter] + prev2 * C2[filter]) >> 6
s  = clamp(s, -32768, 32767)
prev2, prev1 = prev1, s
```

Coefficient table, scaled by 64:

| filter | C1 | C2 |
|---|---|---|
| 0 | 0 | 0 |
| 1 | 60 | 0 |
| 2 | 115 | −52 |
| 3 | 98 | −55 |
| 4 | 122 | −60 |

Nibbles are four-bit two's complement, so the value range is −8..7 inclusive.
Both right shifts round toward negative infinity; a truncating division gives a
different sequence and drifts audibly within a few blocks.

Predictor state is per channel and carries across blocks. It is reset only at the
start of a chunk.

Note that the residual scale is arbitrary: multiplying every `nibble << (12-shift)`
by a constant scales the whole output by the same constant and changes nothing
else. The timbre is determined entirely by the coefficient table, which cannot be
recovered from the bitstream — the encoder and decoder must agree a priori. The
table above is the standard one for this codec and reproduced the expected
spectral shape on all four titles tested.

### 3.3 Nibble order

Established using blocks with `filter == 0`, where no prediction is applied and
the decoded sample equals the scaled nibble directly. Over 126 such blocks the
mean absolute first difference normalised by RMS was 0.0695 for low-nibble-first
against 0.1047 for high-nibble-first; the high-order reading inserts isolated
reversals into what is otherwise a monotonic ramp.

## 4. Channel assignment

Consecutive blocks alternate between channels: even block index → left, odd →
right. Each block is 28 samples of one channel, so both channels share one
timeline at the header's sample rate and the duration is
`total_blocks / 2 * 28 / rate`.

Three independent observations support this:

1. Core header field 4 equals half the block count of the physical span, on
   every chunk. The field is a per-channel count, which is only meaningful if
   blocks belong to one channel at a time.
2. Decoding the stream as a single channel produces an audible alternation
   artefact at `rate / 56` Hz while leaving pitch and track boundaries correct.
   Splitting by block parity removes it.
3. After splitting, the two channels correlate between 0.26 and 0.95 depending on
   the track, i.e. they are related but not identical, as stereo material is.
   A fold-down also roughly doubles the sample-to-sample roughness relative to
   the per-channel values.

Which parity is left and which is right cannot be determined from the data. This
implementation assigns even to left; a mirrored stereo image is possible and
would be inaudible on correlated material.

Two alternative mappings are implemented but unverified on real data:
`pair` (nibbles alternate between channels inside a block) and `mono` (every
block belongs to one channel).

## 5. Verified configurations

| Title | Container | Rate | Chunks | Total | BGM start |
|---|---|---|---|---|---|
| RTK VII | `SAN7BGM.ADX` | 32000 | 27 | 42.19 min | `0x00000000` |
| RTK VIII | `RTK8BGM.ADX` | 44100 | 34 | 30.14 min | `0x00000000` |
| RTK IX | `SAN9BGM.ADX` | 44100 | 27 | 37.49 min | `0x00000000` |
| RTK X | `STREAM.BIN` | 44100 | 38 | 53.41 min | `0x10403000` |

RTK X does not ship a dedicated BGM file. The chunks sit inside `STREAM.BIN`, a
`LINK`-magic archive that also holds PSS movie streams (`SShd`/`SSbd`). Because
chunk discovery is pattern-based rather than filename-based, the same scan works
on the archive directly.

Encoder statistics are stable across all four, which is evidence of a shared
encoder rather than a shared file format alone:

| | RTK VII | RTK VIII | RTK IX | RTK X |
|---|---|---|---|---|
| mean abs nibble | 1.997 | 2.008 | 2.010 | 2.024 |
| RMS nibble | 2.533 | 2.540 | 2.544 | 2.560 |
| nibbles at 0 | 15.96 % | 15.77 % | 15.73 % | 15.88 % |
| blocks with max abs nibble 7 | 15.2 % | 15.1 % | 14.9 % | — |

RTK VII is quantised more coarsely than the rest: its `shift` values sit about
three steps lower, so quantisation noise is roughly 10 dB higher. That is a
property of the source data, not of the decoder.

## 6. Open items

- Left/right parity, as noted in section 4.
- The coefficient table is assumed, not observed. A title using a different
  table would decode with correct pitch and timing but wrong timbre.
- Titles outside this engine lineage may use unrelated formats. The scanner
  reports no chunks rather than producing output in that case.
- No loop points, fade metadata or per-track names have been found. Track order
  is chunk order.
