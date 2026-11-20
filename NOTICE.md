# Notice

## Scope of this repository

This repository contains a decoder and a format description. It contains **no
game data**: no audio, no disc images, no extracted tracks, no sample blocks
lifted from any title, and no code or disassembly from any game or from any
vendor library or SDK.

The test suite generates its own PS-ADPCM streams from synthetic waveforms at
run time. Nothing it uses is derived from a product.

## Affiliation

This project is not affiliated with, authorised by, sponsored by or endorsed by
Koei Tecmo Holdings, Koei Tecmo Games, Sony Interactive Entertainment, or any of
their subsidiaries. It is an independent interoperability tool.

Game titles and platform names referenced in documentation are trademarks or
registered trademarks of their respective owners. They are used descriptively, to
state which titles the implementation has been tested against.

## Intended use

The decoder is intended for use with discs or disc images that the user owns, for
personal study, archival, accessibility and preservation purposes, and for
interoperability with players and analysis tools that do not support the format.

It is not intended to enable distribution of copyrighted audio. Extracted audio
remains the property of its rights holders. Do not redistribute extracted audio,
disc images, or archives containing them.

## Technical basis

Two properties are relevant to the legal character of the tool:

1. **No protection measure is circumvented.** The containers are plain
   concatenations of headers and sample blocks. There is no encryption, no
   obfuscation, no authentication and no access control on the audio data. The
   tool reads a file format; it does not break a technical protection measure.
2. **The codec is not encumbered.** The sample format is PS-ADPCM, a standard
   block ADPCM scheme published in the mid-1990s. Any patents that may have
   covered it have expired. The predictor coefficient table used here is the
   published standard table for that codec.

The format documentation describes byte layouts and field semantics. It was
written from observation and contains no material copied from vendor sources.

## Licensing

The source code in this repository is licensed under the MIT License; see
`LICENSE`. The format description in `FORMAT.md` is provided for
interoperability, under the same terms.

Nothing in this repository grants any rights to any game, game data, audio
recording or musical composition.

## No warranty

The software is provided "as is", without warranty of any kind. See `LICENSE` for
the full disclaimer.

## Reporting

If you believe this repository contains material that infringes your rights,
open an issue or contact the maintainer directly. Claims will be reviewed and
content removed where warranted.
