# SPDX-License-Identifier: GPL-2.0-only
"""Cover routing, log retention, and update consent with isolated fixtures."""
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
import cli_update
# Keep an unpatched installer entry point for the separate rollback scenarios.
INSTALL_RELEASE = cli_update.install_release


class WorkflowTests(unittest.TestCase):
    """Test routing and log behavior without invoking real media encoders."""

    def setUp(self):
        """Stub metadata/validation and confine all files to a temporary root."""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(compress, 'ROOT', self.root).start()
        self.source = self.root / 'source.mov'
        self.source.write_bytes(b'x' * 50)
        self.info = dict(video={'codec_name': 'h264'}, audio=[])
        mock.patch.object(compress, 'probe', return_value=self.info).start()
        mock.patch.object(compress, 'verify').start()
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()
        self.addCleanup(self.console.__exit__, None, None, None)

    def test_small_h264_remuxes_without_transcoding(self):
        def pack(source, info, output, log):
            output.write_bytes(b'packed')
        with mock.patch.object(compress, 'remux', side_effect=pack), mock.patch.object(compress, 'transcode') as transcode:
            output = compress.compress(self.source, 100)
        self.assertEqual(output.read_bytes(), b'packed')
        transcode.assert_not_called()

    def test_remux_at_limit_falls_back_and_never_publishes_oversize(self):
        def pack(source, info, output, log):
            output.write_bytes(b'x' * 100)
        def encode(source, info, limit, working, log, *, probes=None):
            best = working / 'best.mp4'
            best.write_bytes(b'fits')
            return best
        with mock.patch.object(compress, 'remux', side_effect=pack), mock.patch.object(compress, 'transcode', side_effect=encode) as transcode:
            output = compress.compress(self.source, 100)
        self.assertEqual(output.read_bytes(), b'fits')
        transcode.assert_called_once()
        self.assertIn('remux_oversize', next((self.root / 'logs').glob('*.log')).read_text())

    def test_small_non_h264_transcodes(self):
        self.info['video']['codec_name'] = 'hevc'
        sentinel = RuntimeError('transcode reached')
        with mock.patch.object(compress, 'remux') as remux, mock.patch.object(compress, 'transcode', side_effect=sentinel):
            with self.assertRaisesRegex(RuntimeError, 'transcode reached'):
                compress.compress(self.source, 100)
        remux.assert_not_called()

    def test_source_exactly_at_limit_transcodes(self):
        self.source.write_bytes(b'x' * 100)
        with mock.patch.object(compress, 'remux') as remux, mock.patch.object(compress, 'transcode', side_effect=RuntimeError('transcode reached')):
            with self.assertRaisesRegex(RuntimeError, 'transcode reached'):
                compress.compress(self.source, 100)
        remux.assert_not_called()

    def test_retention_covers_legacy_names_and_preserves_unrelated_files(self):
        logs = self.root / 'logs'
        logs.mkdir()
        names = ['encode-old.log', '2026-11-01_12-30-00__process__abcd1234.log',
                 '2026-11-01_12-30-00__update__abcd1234.log', 'notes.log', 'encode-boundary.log']
        now = 2_000_000_000
        cutoff = now - 7 * 86400
        for name in names:
            path = logs / name
            path.write_text('log')
            stamp = cutoff if name == 'encode-boundary.log' else cutoff - 1
            os.utime(path, (stamp, stamp))
        target = self.root / 'external.log'
        target.write_text('external')
        (logs / 'encode-symlink.log').symlink_to(target)
        self.assertEqual(compress.clean_logs(now=now), 3)
        self.assertTrue((logs / 'notes.log').exists())
        self.assertTrue((logs / 'encode-boundary.log').exists())
        self.assertTrue((logs / 'encode-symlink.log').is_symlink())
        self.assertTrue(target.exists())

    def test_timestamp_logs_are_unique_and_record_errors_without_source_in_name(self):
        for _ in range(2):
            with mock.patch.object(compress, 'process_video', side_effect=RuntimeError('specific failure')):
                with self.assertRaises(RuntimeError):
                    compress.compress(self.source, 100)
        logs = list((self.root / 'logs').glob('*.log'))
        self.assertEqual(len(logs), 2)
        for path in logs:
            self.assertRegex(path.name, r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}__diagnostic__process__.+\.log$')
            self.assertNotIn('source', path.name)
            self.assertIn('specific failure', path.read_text())


class UpdateTests(unittest.TestCase):
    """Verify consent decisions and installer failures without networking."""

    def setUp(self):
        """Provide a newer mock release and intercept update side effects."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.release = dict(version='1.12.0', url='https://github.com/HandBrake/HandBrake/releases/download/1.12.0/HandBrakeCLI-1.12.0.dmg', sha256='0' * 64)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(cli_update, 'binary_version', return_value='1.11.2').start()
        mock.patch.object(cli_update, 'latest_release', return_value=self.release).start()
        self.install = mock.patch.object(cli_update, 'install_release').start()
        mock.patch.object(cli_update.sys, 'stdin').start().isatty.return_value = True
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()
        self.addCleanup(self.console.__exit__, None, None, None)

    def test_default_decline_never_downloads_or_installs(self):
        with mock.patch('builtins.input', return_value=''):
            self.assertEqual(cli_update.check_for_updates(Path('CLI'), self.root), 'declined')
        self.install.assert_not_called()

    def test_only_explicit_yes_installs(self):
        with mock.patch('builtins.input', return_value='y'):
            self.assertEqual(cli_update.check_for_updates(Path('CLI'), self.root), 'updated')
        self.install.assert_called_once()

    def test_noninteractive_never_installs(self):
        cli_update.sys.stdin.isatty.return_value = False
        with mock.patch('builtins.input', side_effect=AssertionError('must not prompt')):
            self.assertEqual(cli_update.check_for_updates(Path('CLI'), self.root), 'deferred')
        self.install.assert_not_called()

    def test_current_version_never_prompts(self):
        self.release['version'] = '1.11.2'
        with mock.patch('builtins.input', side_effect=AssertionError('must not prompt')):
            self.assertEqual(cli_update.check_for_updates(Path('CLI'), self.root), 'current')
        self.install.assert_not_called()

    def test_network_failure_does_not_fail_finished_video(self):
        with mock.patch.object(cli_update, 'latest_release', side_effect=TimeoutError('offline')):
            self.assertEqual(cli_update.check_for_updates(Path('CLI'), self.root), 'failed')
        self.install.assert_not_called()

    def test_bad_download_digest_is_rejected(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = [b'wrong payload', b'']
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(cli_update.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                cli_update.download(self.release, Path(directory) / 'download.dmg')

    def test_installer_validation_failure_preserves_old_cli(self):
        self._installer_failure(compatibility_error=True)

    def test_installed_version_failure_restores_backup(self):
        self._installer_failure(compatibility_error=False)

    def _installer_failure(self, compatibility_error):
        """Emulate a mounted candidate and assert the original CLI survives."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bin').mkdir()
            binary = root / 'bin/HandBrakeCLI'
            binary.write_bytes(b'original CLI')

            def fake_hdiutil(command, **kwargs):
                if 'attach' in command:
                    mount = Path(command[command.index('-mountpoint') + 1])
                    (mount / 'HandBrakeCLI').write_bytes(b'new CLI')
                return mock.Mock(returncode=0)

            validation = ValueError('incompatible preset') if compatibility_error else None
            with mock.patch.object(cli_update, 'download'), mock.patch.object(cli_update.subprocess, 'run', side_effect=fake_hdiutil), mock.patch.object(cli_update, 'validate_candidate', side_effect=validation):
                # binary_version is mocked to return 1.11.2, so the final check
                # against the proposed 1.12.0 exercises automatic rollback.
                with self.assertRaises(ValueError):
                    INSTALL_RELEASE(self.release, binary, root)
            self.assertEqual(binary.read_bytes(), b'original CLI')
            self.assertFalse(list((root / 'work').iterdir()))


if __name__ == '__main__':
    unittest.main()
