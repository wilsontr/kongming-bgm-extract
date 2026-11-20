"""End-to-end CLI tests against synthetic containers."""

import json
import math
import os
import subprocess
import sys
import wave

import pytest

from kongming_bgm.cli import main

import synth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")


@pytest.fixture
def archive(tmp_path):
    left = synth.sine(28 * 20, 220.0, 32000, 9000)
    right = synth.sine(28 * 20, 660.0, 32000, 9000)
    padded = [0] * 14000 + synth.sine(28 * 10, 300.0, 32000, 6000) + [0] * 14000
    data, _ = synth.build_container([(left, right), (padded, padded)], sample_rate=32000)
    path = tmp_path / "synthetic.adx"
    path.write_bytes(data)
    return path


def test_scan_json(archive, capsys):
    assert main(["scan", str(archive), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 2
    assert rows[0]["sample_rate"] == 32000
    assert rows[0]["channels"] == 2
    assert rows[0]["adpcm_score"] == 1.0
    assert rows[1]["offset"] > rows[0]["offset"]


def test_scan_table(archive, capsys):
    assert main(["scan", str(archive)]) == 0
    out = capsys.readouterr().out
    assert "blocks/ch" in out
    assert "2 chunks" in out


def test_extract_writes_stereo_wav(archive, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["extract", str(archive), "-o", str(out), "--quiet"]) == 0
    files = sorted(os.listdir(out))
    assert "track_00.wav" in files and "track_01.wav" in files

    with wave.open(str(out / "track_00.wav"), "rb") as handle:
        assert handle.getnchannels() == 2
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 32000
        assert handle.getnframes() == 28 * 20


def test_extract_json_manifest(archive, tmp_path):
    out = tmp_path / "out"
    assert main(["extract", str(archive), "-o", str(out), "--json", "--quiet"]) == 0
    manifest = json.loads((out / "tracks.json").read_text())
    assert len(manifest) == 2
    assert manifest[0]["channels"] == 2
    assert manifest[0]["seconds"] == pytest.approx(28 * 20 / 32000, abs=1e-3)


def test_extract_trim_shortens_track(archive, tmp_path):
    plain = tmp_path / "plain"
    trimmed = tmp_path / "trimmed"
    assert main(["extract", str(archive), "-o", str(plain), "--quiet"]) == 0
    assert main(["extract", str(archive), "-o", str(trimmed), "--trim", "--quiet"]) == 0
    with wave.open(str(plain / "track_01.wav"), "rb") as handle:
        full = handle.getnframes()
    with wave.open(str(trimmed / "track_01.wav"), "rb") as handle:
        short = handle.getnframes()
    assert short < full
    # Trim keeps a 250 ms margin either side of the content.
    assert short > 28 * 10


def test_extract_verify_prints_metrics(archive, tmp_path, capsys):
    out = tmp_path / "out"
    assert main(["extract", str(archive), "-o", str(out), "--verify"]) == 0
    out_text = capsys.readouterr().out
    assert "flatness=" in out_text
    assert "corrLR=" in out_text


def test_rate_override(archive, tmp_path):
    out = tmp_path / "out"
    assert main(["extract", str(archive), "-o", str(out), "--rate", "44100", "--quiet"]) == 0
    with wave.open(str(out / "track_00.wav"), "rb") as handle:
        assert handle.getframerate() == 44100


def test_no_chunks_returns_error(tmp_path, capsys):
    path = tmp_path / "junk.bin"
    path.write_bytes(bytes(range(256)) * 64)
    assert main(["scan", str(path)]) == 1
    assert "no chunks found" in capsys.readouterr().err


def test_module_entry_point():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "kongming_bgm", "--version"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "kongming-bgm" in result.stdout


def test_help_lists_subcommands():
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-m", "kongming_bgm", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "scan" in result.stdout
    assert "extract" in result.stdout
