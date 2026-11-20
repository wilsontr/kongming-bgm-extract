"""Command line interface: ``kongming-bgm scan`` and ``kongming-bgm extract``."""

import argparse
import json
import mmap
import os
import sys

from . import __version__
from .codec import BLOCK_SIZE, SAMPLES_PER_BLOCK
from .container import (
    INTERLEAVE_MODES,
    audio_end_for_chunks,
    decode_chunk,
    find_chunks,
    validation_score,
)
from .diagnostics import analyse
from .wav import write_wav

MMAP_THRESHOLD = 64 << 20


class Source:
    """Read-only view over an input file, memory-mapped when large."""

    def __init__(self, path):
        self.path = path
        self._handle = open(path, "rb")
        size = os.fstat(self._handle.fileno()).st_size
        self.size = size
        if size >= MMAP_THRESHOLD:
            self.data = mmap.mmap(self._handle.fileno(), 0, access=mmap.ACCESS_READ)
            self._mapped = True
        else:
            self.data = self._handle.read()
            self._mapped = False

    def close(self):
        if self._mapped:
            self.data.close()
        self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _chunks_of(source, validate):
    return find_chunks(source.data, validate=validate)


def _duration(blocks_per_channel, rate):
    if not rate:
        return 0.0
    return blocks_per_channel * SAMPLES_PER_BLOCK / rate


def _cmd_scan(args):
    rows = []
    status = 0
    for path in args.inputs:
        with Source(path) as source:
            chunks = _chunks_of(source, not args.no_validate)
            if not chunks:
                print("no chunks found in %s" % path, file=sys.stderr)
                status = 1
                continue
            for index, header in enumerate(chunks):
                end = audio_end_for_chunks(chunks, source.size, index)
                blocks = (end - header.audio_offset) // BLOCK_SIZE
                rows.append(
                    {
                        "input": path,
                        "index": index,
                        "offset": header.offset,
                        "sample_rate": header.sample_rate,
                        "channels": header.channels,
                        "bit_depth": header.bit_depth,
                        "blocks_per_channel": header.blocks_per_channel,
                        "audio_offset": header.audio_offset,
                        "audio_end": end,
                        "seconds": round(_duration(header.blocks_per_channel, header.sample_rate), 3),
                        "adpcm_score": round(validation_score(source.data, header), 3),
                        "blocks_decoded": blocks,
                    }
                )
    if args.json:
        json.dump(rows, sys.stdout, indent=1)
        sys.stdout.write("\n")
    else:
        if not rows:
            return status
        print(
            "%-4s %-10s %-6s %-4s %-8s %-11s %-7s %s"
            % ("#", "offset", "rate", "ch", "blocks/ch", "seconds", "adpcm", "input")
        )
        for row in rows:
            print(
                "%-4d 0x%08X %-6d %-4d %-8d %-11.3f %-7.3f %s"
                % (
                    row["index"],
                    row["offset"],
                    row["sample_rate"],
                    row["channels"],
                    row["blocks_per_channel"],
                    row["seconds"],
                    row["adpcm_score"],
                    os.path.basename(row["input"]),
                )
            )
        total = {}
        for row in rows:
            total[row["input"]] = total.get(row["input"], 0.0) + row["seconds"]
        for path, seconds in total.items():
            print("%s: %d chunks, %.2f s (%.2f min)" % (path, sum(1 for r in rows if r["input"] == path), seconds, seconds / 60))
    return status


def _cmd_extract(args):
    records = []
    status = 0
    for path in args.inputs:
        with Source(path) as source:
            chunks = _chunks_of(source, not args.no_validate)
            if not chunks:
                print("no chunks found in %s" % path, file=sys.stderr)
                status = 1
                continue
            os.makedirs(args.output, exist_ok=True)
            base = len(records)
            for index, header in enumerate(chunks):
                end = audio_end_for_chunks(chunks, source.size, index)
                pair = decode_chunk(source.data, header, end, interleave=args.interleave)
                if args.rate:
                    pair.sample_rate = args.rate
                name = "track_%02d.wav" % (base + index)
                target = os.path.join(args.output, name)
                written = write_wav(target, pair, trim=args.trim)
                record = {
                    "track": base + index,
                    "input": path,
                    "offset": header.offset,
                    "sample_rate": pair.sample_rate,
                    "channels": written["channels"],
                    "frames": written["samples"],
                    "seconds": round(written["samples"] / pair.sample_rate, 3)
                    if pair.sample_rate
                    else 0.0,
                    "blocks_per_channel": header.blocks_per_channel,
                    "trimmed_lead_frames": written["trimmed_lead_frames"],
                    "trimmed_tail_frames": written["trimmed_tail_frames"],
                    "file": target,
                }
                if args.verify:
                    record["metrics"] = analyse(pair)
                records.append(record)
                if not args.quiet:
                    print(
                        "track %02d  0x%08X  %d Hz  %7.2f s  %s"
                        % (
                            base + index,
                            header.offset,
                            pair.sample_rate,
                            record["seconds"],
                            target,
                        )
                    )
                    if args.verify:
                        print("    " + _format_metrics(record["metrics"]))
    if args.json:
        manifest = os.path.join(args.output, "tracks.json")
        os.makedirs(args.output, exist_ok=True)
        with open(manifest, "w") as handle:
            json.dump(records, handle, indent=1)
            handle.write("\n")
        if not args.quiet:
            print("manifest: %s" % manifest)
    if records and not args.quiet:
        seconds = sum(record["seconds"] for record in records)
        print("%d tracks, %.2f s (%.2f min)" % (len(records), seconds, seconds / 60))
    return status


def _format_metrics(metrics):
    parts = []
    if metrics.get("rms") is not None:
        parts.append("rms=%.0f" % metrics["rms"])
    if metrics.get("peak") is not None:
        parts.append("peak=%d" % metrics["peak"])
    if metrics.get("clipped_samples"):
        parts.append("clipped=%d" % metrics["clipped_samples"])
    if metrics.get("roughness") is not None:
        parts.append("roughness=%.3f" % metrics["roughness"])
    if metrics.get("correlation_lr") is not None:
        parts.append("corrLR=%+.3f" % metrics["correlation_lr"])
    if metrics.get("spectral_flatness") is not None:
        parts.append("flatness=%.5f" % metrics["spectral_flatness"])
    if metrics.get("low_band_ratio") is not None:
        parts.append("below2k=%.1f%%" % (100.0 * metrics["low_band_ratio"]))
    return "  ".join(parts)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="kongming-bgm",
        description="Decode chunked PS-ADPCM BGM containers from Koei PS2 titles.",
    )
    parser.add_argument("--version", action="version", version="kongming-bgm %s" % __version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="list chunks without writing audio")
    scan.add_argument("inputs", nargs="+", metavar="FILE")
    scan.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    scan.add_argument(
        "--no-validate",
        action="store_true",
        help="accept candidate headers without checking the following blocks",
    )
    scan.set_defaults(handler=_cmd_scan)

    extract = subparsers.add_parser("extract", help="decode chunks to WAV")
    extract.add_argument("inputs", nargs="+", metavar="FILE")
    extract.add_argument("-o", "--output", default="tracks", help="output directory")
    extract.add_argument(
        "--interleave",
        choices=INTERLEAVE_MODES,
        default="block",
        help="channel mapping (default: block, verified on all titles tested)",
    )
    extract.add_argument("--rate", type=int, default=0, help="override the output sample rate")
    extract.add_argument("--trim", action="store_true", help="remove leading and trailing silence")
    extract.add_argument("--verify", action="store_true", help="print verification metrics per track")
    extract.add_argument("--json", action="store_true", help="also write tracks.json")
    extract.add_argument("--quiet", action="store_true", help="suppress per-track output")
    extract.add_argument(
        "--no-validate",
        action="store_true",
        help="accept candidate headers without checking the following blocks",
    )
    extract.set_defaults(handler=_cmd_extract)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
