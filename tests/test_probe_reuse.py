# SPDX-License-Identifier: GPL-2.0-only
"""Check probe counts, candidate invalidation, and final validation."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import compress


def metadata(codec='h264', width=320):
    """Return synthetic metadata independent of container bytes."""
    return dict(video={'codec_name': codec, 'avg_frame_rate': '30/1',
                       'nb_frames': '60'}, duration=2, dimensions=(width, 180),
                audio=[])


class ProbeReuseTests(unittest.TestCase):
    """Count probes while exercising real routing and validation."""

    def setUp(self):
        """Use private synthetic files and stub media subprocesses."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / 'source.mov'
        self.source.write_bytes(b'synthetic source')
        self.codec = 'h264'
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(compress, 'ROOT', self.root).start()
        mock.patch.object(compress, 'tool', return_value='unused').start()
        self.probe = mock.patch.object(compress, 'probe', side_effect=self.inspect).start()
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        errors = contextlib.redirect_stderr(io.StringIO())
        errors.__enter__()
        self.addCleanup(errors.__exit__, None, None, None)

    def inspect(self, path):
        """Distinguish the input codec from encoded outputs."""
        return metadata(self.codec if path == self.source else 'h264')

    def transcode_sizes(self, sizes):
        """Drive bitrate retries using synthetic output sizes."""
        self.codec = 'mpeg4'
        attempts = iter(sizes)

        def encode(command, log):
            candidate = Path(command[command.index('-o') + 1])
            candidate.write_bytes(b'v' * next(attempts))

        with mock.patch.object(compress, 'encode', side_effect=encode) as encoder:
            output = compress.compress(self.source, 100_000)
        self.assertEqual(encoder.call_count, len(sizes))
        return output

    def test_remux_probes_only_source_and_output(self):
        def remux(source, info, output, log):
            output.write_bytes(b'packed video')

        with mock.patch.object(compress, 'remux', side_effect=remux):
            output = compress.compress(self.source, 100_000)
        self.assertEqual(output.read_bytes(), b'packed video')
        self.assertEqual(self.probe.call_count, 2)

    def test_single_encode_probes_only_source_and_candidate(self):
        output = self.transcode_sizes([99_500])
        self.assertEqual(output.stat().st_size, 99_500)
        self.assertEqual(self.probe.call_count, 2)

    def test_smaller_later_candidate_does_not_replace_validated_best(self):
        output = self.transcode_sizes([98_000, 97_000])
        self.assertEqual(output.stat().st_size, 98_000)
        self.assertEqual(self.probe.call_count, 3)

    def test_oversize_attempt_is_probed_once_and_not_published(self):
        output = self.transcode_sizes([101_000, 99_500])
        self.assertEqual(output.stat().st_size, 99_500)
        self.assertEqual(self.probe.call_count, 3)

    def test_same_size_rewrite_with_restored_mtime_is_reprobed(self):
        candidate = self.root / 'candidate.mp4'
        candidate.write_bytes(b'aaaa')
        cache = compress.ProbeCache()
        first = cache.read(candidate)
        before = candidate.stat()
        candidate.write_bytes(b'bbbb')
        os.utime(candidate, ns=(before.st_atime_ns, before.st_mtime_ns))
        second = cache.read(candidate)
        self.assertIsNot(first, second)
        self.assertEqual(self.probe.call_count, 2)

    def test_replacement_with_same_size_and_mtime_is_reprobed(self):
        candidate = self.root / 'candidate.mp4'
        candidate.write_bytes(b'aaaa')
        cache = compress.ProbeCache()
        first = cache.read(candidate)
        before = candidate.stat()
        replacement = self.root / 'replacement.mp4'
        replacement.write_bytes(b'bbbb')
        os.utime(replacement, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(replacement, candidate)
        second = cache.read(candidate)
        self.assertIsNot(first, second)
        self.assertEqual(self.probe.call_count, 2)

    def test_changed_during_probe_is_rejected_and_not_cached(self):
        candidate = self.root / 'candidate.mp4'
        candidate.write_bytes(b'first')

        def changing_probe(path):
            path.write_bytes(b'changed during probe')
            return metadata()

        cache = compress.ProbeCache()
        self.probe.side_effect = changing_probe
        with self.assertRaisesRegex(RuntimeError, '探测期间文件发生变化'):
            cache.read(candidate)
        self.probe.side_effect = self.inspect
        cache.read(candidate)
        self.assertEqual(self.probe.call_count, 2)

    def test_timeout_is_not_cached(self):
        candidate = self.root / 'candidate.mp4'
        candidate.write_bytes(b'encoded')
        self.probe.side_effect = [RuntimeError('probe timed out'), metadata()]
        cache = compress.ProbeCache()
        with self.assertRaisesRegex(RuntimeError, 'probe timed out'):
            cache.read(candidate)
        self.assertEqual(cache.read(candidate)['video']['codec_name'], 'h264')
        self.assertEqual(self.probe.call_count, 2)

    def test_cached_metadata_still_checks_size_and_geometry(self):
        candidate = self.root / 'candidate.mp4'
        candidate.write_bytes(b'v' * 90)
        cache = compress.ProbeCache()
        compress.verify(metadata(), candidate, 100, probes=cache)
        with self.assertRaisesRegex(RuntimeError, '大小限制'):
            compress.verify(metadata(), candidate, 90, probes=cache)
        with self.assertRaisesRegex(RuntimeError, '分辨率发生变化'):
            compress.verify(metadata(width=640), candidate, 100, probes=cache)
        self.assertEqual(self.probe.call_count, 1)

    def test_changed_best_is_reprobed_before_publication(self):
        transcode = compress.transcode
        self.codec = 'mpeg4'

        def changed_best(*args, **kwargs):
            best = transcode(*args, **kwargs)
            best.write_bytes(b'X' * 99_500)
            return best

        def inspect(path):
            if path != self.source and path.read_bytes().startswith(b'X'):
                return metadata(width=640)
            return self.inspect(path)

        def encode(command, log):
            Path(command[command.index('-o') + 1]).write_bytes(b'v' * 99_500)

        self.probe.side_effect = inspect
        with mock.patch.object(compress, 'encode', side_effect=encode), \
                mock.patch.object(compress, 'transcode', side_effect=changed_best):
            with self.assertRaisesRegex(RuntimeError, '分辨率发生变化'):
                compress.compress(self.source, 100_000)
        self.assertFalse((self.root / 'source_compressed.mp4').exists())
        self.assertEqual(self.probe.call_count, 3)


if __name__ == '__main__':
    unittest.main()
