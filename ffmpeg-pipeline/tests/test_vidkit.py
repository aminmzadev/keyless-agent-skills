#!/usr/bin/env python3
"""Tests for the ffmpeg wrappers that do not need ffmpeg to run.

The bug this file exists for: `duration()` used to return 0.0 whenever ffprobe
could not parse a length. Every caller then compared that 0 against the other
track and took the "audio is shorter" branch, so a corrupt mp3 was silently
replaced by silence and the command exited 0 with an unmuxed video.

Run:  python3 ffmpeg-pipeline/tests/test_vidkit.py -v
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIDKIT_PY = ROOT / "ffmpeg-pipeline" / "scripts" / "vidkit.py"


def _load_vidkit():
    spec = importlib.util.spec_from_file_location("ffmpeg_vidkit", VIDKIT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


V = _load_vidkit()


class _Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class DurationTests(unittest.TestCase):
    def setUp(self):
        self._need, self._run = V.need, V.run
        V.need = lambda binary: None           # pretend ffprobe is installed

    def tearDown(self):
        V.need, V.run = self._need, self._run  # both are stubbed per test

    def test_a_real_length_is_returned(self):
        self._stub(_Done(stdout="12.500000\n"))
        self.assertEqual(V.duration("x.mp4"), 12.5)

    def test_an_unparseable_length_is_an_error_not_a_zero(self):
        self._stub(_Done(stdout="N/A\n"))
        with self.assertRaises(RuntimeError) as ctx:
            V.duration("broken.mp3")
        self.assertIn("no duration", str(ctx.exception))

    def test_an_empty_length_is_an_error(self):
        self._stub(_Done(stdout=""))
        with self.assertRaises(RuntimeError):
            V.duration("empty.mp3")

    def test_zero_length_is_an_error(self):
        self._stub(_Done(stdout="0\n"))
        with self.assertRaises(RuntimeError):
            V.duration("zero.mp4")

    def test_a_missing_ffprobe_is_reported_with_the_install_hint(self):
        V.need = self._need
        real_which = V.shutil.which
        V.shutil.which = lambda b: None
        try:
            with self.assertRaises(SystemExit) as ctx:
                V.duration("x.mp4")
            self.assertIn("ffprobe", str(ctx.exception))
        finally:
            V.shutil.which = real_which

    def _stub(self, done):
        V.run = lambda cmd, **kw: done         # noqa: E731 - replaced per test


class RunTests(unittest.TestCase):
    def test_a_hanging_encoder_is_cut_off(self):
        """No terminal means no Ctrl-C: the call needs its own wall clock."""
        real = V.subprocess.run

        def hang(cmd, timeout=None, **kw):
            raise subprocess.TimeoutExpired(cmd, timeout or 0)

        V.subprocess.run = hang
        try:
            with self.assertRaises(RuntimeError) as ctx:
                V.run(["ffmpeg", "-version"], timeout=5)
            self.assertIn("gave up", str(ctx.exception))
        finally:
            V.subprocess.run = real

    def test_a_failure_carries_the_last_stderr_line(self):
        done = _Done(stderr="line one\nInvalid data found\n", returncode=1)
        real = V.subprocess.run
        V.subprocess.run = lambda cmd, **kw: done
        try:
            with self.assertRaises(RuntimeError) as ctx:
                V.run(["ffmpeg", "-i", "x.mp4"])
            self.assertIn("Invalid data found", str(ctx.exception))
        finally:
            V.subprocess.run = real

    def test_the_timeout_is_configurable_from_the_environment(self):
        script = (
            "import importlib.util,os;"
            f"spec=importlib.util.spec_from_file_location('v',r'{VIDKIT_PY}');"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
            "print(m.TIMEOUT)")
        out = subprocess.run([os.sys.executable, "-c", script], capture_output=True,
                             text=True, env={**os.environ, "VIDKIT_FFMPEG_TIMEOUT": "42"})
        self.assertEqual(out.stdout.strip(), "42", out.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
