# SPDX-License-Identifier: GPL-2.0-only
"""Regression coverage for SMB-safe saving and recoverable encode results."""
import errno
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('compress', Path(__file__).resolve().parents[1] / 'compress.py')
compress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compress)


class PublishTests(unittest.TestCase):
    """Cover copy integrity, non-overwriting names, and local recovery."""

    def setUp(self):
        """Create isolated candidate/source paths for publication scenarios."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.encoded = self.root / 'best.mp4'
        self.encoded.write_bytes(b'encoded video' * 1000)
        self.source = self.root / '中文 video.mov'

    def test_save_without_hardlinks_preserves_existing_output(self):
        existing = self.root / '中文 video_compressed.mp4'
        existing.write_bytes(b'existing output')
        with mock.patch.object(compress.os, 'link', side_effect=OSError(errno.ENOTSUP, 'SMB')):
            saved = compress.publish(self.encoded, self.source)
        self.assertEqual(saved.name, '中文 video_compressed_2.mp4')
        self.assertEqual(saved.read_bytes(), self.encoded.read_bytes())
        self.assertEqual(existing.read_bytes(), b'existing output')

    def test_failed_copy_removes_only_its_partial_output(self):
        def interrupted_copy(source, destination, **kwargs):
            destination.write(b'partial')
            raise OSError(errno.EIO, 'NAS disconnected')
        with mock.patch.object(compress.shutil, 'copyfileobj', side_effect=interrupted_copy):
            with self.assertRaises(OSError):
                compress.publish(self.encoded, self.source)
        self.assertFalse((self.root / '中文 video_compressed.mp4').exists())
        self.assertTrue(self.encoded.exists())

    def test_corrupt_copy_is_rejected(self):
        def corrupt_copy(source, destination, **kwargs):
            destination.write(b'x' * self.encoded.stat().st_size)
        with mock.patch.object(compress.shutil, 'copyfileobj', side_effect=corrupt_copy):
            with self.assertRaises(OSError):
                compress.publish(self.encoded, self.source)
        self.assertFalse((self.root / '中文 video_compressed.mp4').exists())
        self.assertTrue(self.encoded.exists())

    def test_save_failure_keeps_local_encoded_result(self):
        with mock.patch.object(compress, 'ROOT', self.root):
            with self.assertRaises(OSError):
                with compress.encoding_workspace() as working:
                    (working / 'best.mp4').write_bytes(b'recoverable')
                    raise OSError(errno.EIO, 'NAS disconnected')
        self.assertEqual((working / 'best.mp4').read_bytes(), b'recoverable')


if __name__ == '__main__':
    unittest.main()
