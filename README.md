# kongming-bgm

Decoder for the chunked PS-ADPCM BGM containers shipped with several Koei PS2
strategy titles. Extracts each track to a 16-bit PCM WAV file.

The container is a concatenation of self-describing chunks: a 32-byte core
header, a block of zero padding, then 16-byte PS-ADPCM blocks carrying two
channels in alternating blocks. Neither the sample rate nor the chunk count is
fixed across titles, and the chunks are not always in a dedicated file, so the
decoder scans for them rather than assuming a layout. The full specification is
in [FORMAT.md](FORMAT.md).

## Verified coverage

| Title | Container | Rate | Tracks | Total audio |
|---|---|---|---|---|
| RTK VII | `SAN7BGM.ADX` | 32000 Hz | 27 | 42.19 min |
| RTK VIII | `RTK8BGM.ADX` | 44100 Hz | 34 | 30.14 min |
| RTK IX | `SAN9BGM.ADX` | 44100 Hz | 27 | 37.49 min |
| RTK X | `STREAM.BIN` | 44100 Hz | 38 | 53.41 min |

RTK X does not ship a BGM file; its chunks sit inside an archive that also holds
movie streams. The scan is filename-independent, so it runs against the archive
directly.

Titles outside this engine lineage may use unrelated formats. In that case the
scanner reports no chunks and writes nothing.

## Requirements

Python 3.9 or later. No runtime dependencies.

## Install

```sh
pip install .
```

Or, for development:

```sh
pip install -e ".[dev]"
```

Without installing, the package can be run from a checkout:

```sh
PYTHONPATH=src python3 -m kongming_bgm --help
```

## Usage

### List chunks

```sh
kongming-bgm scan ROM.BIN
```

```
#    offset     rate   ch   blocks/ch seconds     adpcm   input
0    0x00000000 44100  2    150886   95.801      1.000   ROM.BIN
1    0x0049B800 44100  2    57022    36.204      1.000   ROM.BIN
ROM.BIN: 2 chunks, 132.01 s (2.20 min)
```

`--json` emits the same data as a JSON array. `adpcm` is the fraction of sampled
blocks that satisfy the block-format checks described in FORMAT.md; candidates
below 1.000 are discarded unless `--no-validate` is given.

### Extract

```sh
kongming-bgm extract ROM.BIN -o tracks
```

Writes `track_00.wav`, `track_01.wav`, ... in chunk order, as 16-bit stereo PCM
at the rate declared by each chunk header.

| Option | Effect |
|---|---|
| `-o, --output DIR` | output directory (default `tracks`) |
| `--interleave {block,mono,pair}` | channel mapping (default `block`) |
| `--rate HZ` | override the output sample rate |
| `--trim` | remove leading and trailing digital silence, keeping 250 ms either side |
| `--verify` | print verification metrics per track |
| `--json` | also write `tracks.json` |
| `--quiet` | suppress per-track output |
| `--no-validate` | accept chunk candidates without checking the following blocks |

Multiple input files may be given; track numbering continues across them.

### Verifying a decode

```sh
kongming-bgm extract ROM.BIN -o tracks --verify
```

```
    rms=4021  peak=30943  roughness=0.157  corrLR=+0.647  flatness=0.00483  below2k=93.4%
```

These metrics exist to make a wrong decode visible. Reference ranges from
correctly decoded material:

| Metric | Correct decode | What a fault looks like |
|---|---|---|
| `roughness` (RMS first difference / RMS) | 0.05 – 0.35 | above 0.5 indicates interleaving or predictor faults |
| `flatness` (spectral flatness) | 0.0002 – 0.03 | above 0.2 means noise rather than music |
| `corrLR` | 0.25 – 0.95 | near 1.0 for every track suggests a mono fold-down |
| `below2k` (energy below 2 kHz) | 0.80 – 0.99 | a large drop suggests a wrong sample rate |

Metrics are computed over a sampled window rather than the whole track.

## Format summary

Each chunk is a 32-byte core header of six `u32` fields, a zero pad of the length
given in the first field, then 16-byte PS-ADPCM blocks:

```
u32[0]  pad length following the header   (2048)
u32[1]  sample rate                       (32000 or 44100)
u32[2]  channel count                     (2)
u32[3]  nominal bit depth                 (16)
u32[4]  chunk length in blocks per channel
u32[5]  format version                    (1)
```

A block is a control byte `(filter << 4) | shift`, a zero byte, then 28
four-bit nibbles packed low-nibble-first. Reconstruction is the standard
two-tap predictive form with a fixed coefficient table. Consecutive blocks
alternate left and right.

[FORMAT.md](FORMAT.md) has the field tables, anchor patterns, reconstruction
recurrence, coefficient table, and the evidence for the channel mapping,
including the parts that remain unresolved.

## Tests

```sh
python3 -m pytest
```

The suite is self-contained: it contains a small PS-ADPCM encoder that generates
valid block streams from synthetic waveforms, plus a container builder. No game
data is used or required.

Reference expectations in `tests/test_codec.py` are computed by hand from the
recurrence in FORMAT.md rather than by calling the decoder twice.

## Limitations

- Which block parity is left versus right cannot be derived from the data. Even
  block is treated as left, so a mirrored stereo image is possible.
- The predictor coefficient table is assumed. It is the standard table for this
  codec and reproduced the expected spectral shape on all titles tested, but the
  bitstream does not constrain it. A title using a different table would decode
  with correct pitch and timing but incorrect timbre.
- Chunk discovery relies on at least one of three anchor patterns (see
  FORMAT.md). A title with pad ≠ 2048, mono channels and non-16-bit nominal depth
  would match none of them.
- `pair` and `mono` channel mappings are implemented but unverified on real data.
- No loop points, fades or track names have been found. Track order is chunk
  order.

## Legal

This repository contains no game data. The test suite uses synthetic material
only.

It is not affiliated with or endorsed by Koei Tecmo or Sony Interactive
Entertainment. Game titles and platform names are trademarks of their respective
owners and are used descriptively.

The codec is a 1990s standard whose patents have expired, and the containers
carry no encryption, obfuscation or access control, so this is a format reader
rather than a circumvention tool. Extracted audio remains the property of its
rights holders; do not redistribute it.

See [NOTICE.md](NOTICE.md) for detail. Code is MIT licensed; see [LICENSE](LICENSE).
