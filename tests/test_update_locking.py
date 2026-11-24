# SPDX-License-Identifier: GPL-2.0-only
"""Verify consent and CLI updates against real process locks."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli_update
import compress


def lock_available(root, *, shared=False):
    """Try the queue lock from another process without waiting."""
    command = '''
import fcntl
from pathlib import Path
import sys
with Path(sys.argv[1]).open('a') as handle:
    mode = fcntl.LOCK_SH if sys.argv[2] == 'shared' else fcntl.LOCK_EX
    try:
        fcntl.flock(handle, mode | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(1)
'''
    result = subprocess.run([sys.executable, '-c', command, str(root / '.queue.lock'),
                             'shared' if shared else 'exclusive'],
                            capture_output=True, text=True, timeout=5)
    if result.returncode not in (0, 1) or result.stderr:
        raise AssertionError(result.stderr or f'Lock probe exited {result.returncode}')
    return result.returncode == 0


class UpdateLockTests(unittest.TestCase):
    """Keep prompts unlocked while serializing executable access."""

    def setUp(self):
        """Use temporary locks and stub external work."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.binary = self.root / 'bin/HandBrakeCLI'
        self.release = dict(version='1.12.0')
        self.addCleanup(mock.patch.stopall)
        self.version = mock.patch.object(cli_update, 'binary_version', return_value='1.11.2').start()
        self.latest = mock.patch.object(cli_update, 'latest_release', return_value=self.release).start()
        self.install = mock.patch.object(cli_update, 'install_release').start()
        mock.patch.object(cli_update.sys, 'stdin').start().isatty.return_value = True
        self.console = contextlib.redirect_stdout(io.StringIO())
        self.console.__enter__()
        self.addCleanup(self.console.__exit__, None, None, None)

    def test_batch_releases_lock_before_calling_updater(self):
        def encode(*args):
            self.assertFalse(lock_available(self.root))

        def check(binary, root):
            self.assertEqual(root, self.root)
            self.assertTrue(lock_available(root))

        with mock.patch.object(compress, 'ROOT', self.root), \
                mock.patch.object(compress, 'compress', side_effect=encode), \
                mock.patch.object(compress, 'tool', return_value=str(self.binary)), \
                mock.patch.object(cli_update, 'check_for_updates', side_effect=check) as updater, \
                mock.patch.object(sys, 'argv', ['clipfittt', 'sample.mp4']):
            self.assertEqual(compress.main(), 0)
        updater.assert_called_once()

    def test_metadata_request_and_prompt_allow_another_batch(self):
        def latest():
            self.assertTrue(lock_available(self.root))
            return self.release

        def answer(prompt):
            self.assertTrue(lock_available(self.root))
            return ''

        self.latest.side_effect = latest
        with mock.patch('builtins.input', side_effect=answer):
            self.assertEqual(cli_update.check_for_updates(self.binary, self.root), 'declined')
        self.install.assert_not_called()

    def test_version_reads_and_installation_are_protected(self):
        reads = []

        def version(binary):
            self.assertFalse(lock_available(self.root))
            # The initial read permits other readers. The pre-install
            # read must already hold the installation's exclusive lock.
            self.assertEqual(lock_available(self.root, shared=True), not reads)
            reads.append(binary)
            return '1.11.2'

        def install(*args):
            self.assertFalse(lock_available(self.root))
            self.assertFalse(lock_available(self.root, shared=True))

        self.version.side_effect = version
        self.install.side_effect = install
        with mock.patch('builtins.input', return_value='yes'):
            self.assertEqual(cli_update.check_for_updates(self.binary, self.root), 'updated')
        self.assertEqual(len(reads), 2)
        self.install.assert_called_once_with(self.release, self.binary, self.root)
        self.assertTrue(lock_available(self.root))

    def test_concurrent_update_is_not_repeated_or_downgraded(self):
        for installed_version in ('1.12.0', '1.13.0'):
            with self.subTest(installed_version=installed_version):
                self.version.side_effect = ['1.11.2', installed_version]
                with mock.patch('builtins.input', return_value='y'):
                    self.assertEqual(cli_update.check_for_updates(self.binary, self.root), 'current')
                self.install.assert_not_called()
                self.assertTrue(lock_available(self.root))

    def test_failed_or_cancelled_update_releases_lock(self):
        for error, status in ((ValueError('incompatible'), 'failed'),
                              (KeyboardInterrupt(), 'cancelled')):
            with self.subTest(status=status):
                self.install.side_effect = error
                with mock.patch('builtins.input', return_value='y'):
                    self.assertEqual(cli_update.check_for_updates(self.binary, self.root), status)
                self.assertTrue(lock_available(self.root))


if __name__ == '__main__':
    unittest.main()
