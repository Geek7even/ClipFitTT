# SPDX-License-Identifier: GPL-2.0-only
"""Test logging privacy, bounded storage, process draining, and fallback."""
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_logging
REAL_NATIVE_BACKEND = app_logging.NativeBackend


class LoggingTests(unittest.TestCase):
    """Keep diagnostics useful without letting logging break media work."""

    def setUp(self):
        """Use a temporary app and a controllable native sink."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.version = (Path(__file__).resolve().parents[1] / 'VERSION').read_text().strip()
        (self.root / 'VERSION').write_text(self.version + '\n')
        self.addCleanup(mock.patch.stopall)
        self.backend = mock.Mock()
        mock.patch.object(app_logging, 'NativeBackend', return_value=self.backend).start()
        for redirect in (contextlib.redirect_stdout, contextlib.redirect_stderr):
            manager = redirect(io.StringIO())
            manager.__enter__()
            self.addCleanup(manager.__exit__, None, None, None)

    def test_native_success_has_no_file_and_public_fields_are_whitelisted(self):
        with app_logging.task_scope(self.root) as task:
            print('TEST_PRIVATE_PATH')
            app_logging.event('publish_completed', output_bytes=90,
                              source='TEST_PRIVATE_PATH', reason='TEST_PRIVATE_REASON',
                              private_detail='TEST_PRIVATE_DETAIL')
        self.assertFalse((self.root / 'logs').exists())
        calls = self.backend.write.call_args_list
        selected = next(c.args for c in calls if c.args[2] == 'publish_completed')
        data = json.loads(selected[5])
        self.assertEqual(data, {'version': self.version, 'output_bytes': 90})
        self.assertEqual(selected[6], 'TEST_PRIVATE_DETAIL')
        self.assertEqual(selected[4], task.task_id)
        self.assertNotIn('TEST_PRIVATE_PATH', str(calls))

    def test_native_failure_falls_back_without_private_transcript(self):
        self.backend.write.side_effect = OSError('native unavailable')
        with app_logging.task_scope(self.root) as task:
            print('TEST_PRIVATE_TRANSCRIPT')
            app_logging.event('probe_completed', private_detail='TEST_PRIVATE_DETAIL')
        text = task.path.read_text()
        self.assertIn('task_completed', text)
        self.assertNotIn('TEST_PRIVATE', text)
        self.assertIn('__fallback__', task.path.name)

    def test_missing_native_backend_uses_safe_file_fallback(self):
        with mock.patch.object(app_logging, 'NativeBackend', side_effect=OSError):
            with app_logging.task_scope(self.root) as task:
                app_logging.event('input_ready', input_bytes=10)
        self.assertIn('input_ready', task.path.read_text())
        self.assertEqual(task.path.stat().st_mode & 0o777, 0o600)

    def test_failure_keeps_bounded_tail_and_preserves_original_exception(self):
        error = ValueError('TEST_PRIVATE_FAILURE')
        with self.assertRaises(ValueError) as caught:
            with app_logging.task_scope(self.root) as task:
                task.capture('EARLY_' + 'x' * (app_logging.DIAGNOSTIC_LIMIT * 4))
                task.capture('LAST_TOOL_MESSAGE\n')
                raise error
        self.assertIs(caught.exception, error)
        self.assertLessEqual(task.path.stat().st_size, app_logging.MAX_FILE_BYTES)
        text = task.path.read_text()
        self.assertIn('LAST_TOOL_MESSAGE', text)
        self.assertIn('TEST_PRIVATE_FAILURE', text)
        self.assertNotIn('EARLY_', text)
        self.assertIn('__diagnostic__', task.path.name)

    def test_unwritable_diagnostics_never_mask_media_failure(self):
        error = RuntimeError('original media failure')
        with mock.patch.object(app_logging.tempfile, 'mkstemp', side_effect=OSError):
            with self.assertRaises(RuntimeError) as caught:
                with app_logging.task_scope(self.root):
                    raise error
        self.assertIs(caught.exception, error)

    def test_process_and_update_share_batch_but_have_different_tasks(self):
        with app_logging.batch_scope(self.root) as batch:
            with app_logging.task_scope(self.root) as process:
                pass
            with app_logging.task_scope(self.root, 'update') as update:
                pass
        self.assertEqual(batch.batch_id, process.batch_id)
        self.assertEqual(process.batch_id, update.batch_id)
        self.assertNotEqual(process.task_id, update.task_id)
        count = self.backend.write.call_count
        app_logging.event('outside_task')
        self.assertEqual(self.backend.write.call_count, count)

    def test_caught_update_error_still_keeps_failure_diagnostics(self):
        with app_logging.task_scope(self.root, 'update') as task:
            print('TEST_PRIVATE_UPDATE_FAILURE')
            app_logging.event('update_failed', level='error', status='failed')
        self.assertIn('__diagnostic__', task.path.name)
        self.assertIn('TEST_PRIVATE_UPDATE_FAILURE', task.path.read_text())

    def test_progress_does_not_flood_diagnostic_buffer(self):
        with app_logging.task_scope(self.root) as task:
            print('\rEncoding: 10%', end='')
            print('USEFUL_MESSAGE')
        self.assertNotIn('Encoding:', task.diagnostics.text())
        self.assertIn('USEFUL_MESSAGE', task.diagnostics.text())

    def test_error_private_detail_is_available_only_in_failure_diagnostics(self):
        with app_logging.task_scope(self.root) as task:
            app_logging.event('validation_failed', level='error',
                              reason='metadata_tags', private_detail='TEST_PRIVATE_TAG')
        self.assertIn('TEST_PRIVATE_TAG', task.path.read_text())
        self.assertNotIn('TEST_PRIVATE_TAG', task.events.text())

    def test_batch_exception_keeps_diagnostics_and_original_error(self):
        error = OSError('TEST_PRIVATE_QUEUE_FAILURE')
        with self.assertRaises(OSError) as caught:
            with app_logging.batch_scope(self.root) as batch:
                raise error
        self.assertIs(caught.exception, error)
        self.assertIn('TEST_PRIVATE_QUEUE_FAILURE', batch.path.read_text())

    def test_broken_terminal_does_not_abort_task(self):
        console = mock.Mock()
        console.write.side_effect = BrokenPipeError
        console.flush.side_effect = BrokenPipeError
        with contextlib.redirect_stdout(console):
            with app_logging.task_scope(self.root) as task:
                print('still runs', flush=True)
        self.assertFalse(task.failed)

    def test_drain_both_pipes_without_deadlock_or_unbounded_storage(self):
        task = app_logging.TaskLog(self.root, 'process')
        script = ('import os, sys\n'
                  'for _ in range(300):\n'
                  ' os.write(1, b"a" * 4096 + b"\\n")\n'
                  ' os.write(2, b"b" * 4096)\n'
                  'sys.exit(7)\n')
        code = app_logging.run_process([sys.executable, '-c', script], task,
                                       tool='ffmpeg')
        self.assertEqual(code, 7)
        self.assertLessEqual(len(task.diagnostics.data), app_logging.DIAGNOSTIC_LIMIT)
        self.assertGreater(task.diagnostics.dropped, 0)
        self.assertFalse(task.failed)

    def test_cancelled_process_is_terminated_and_reaped(self):
        task = app_logging.TaskLog(self.root, 'process')
        real_popen = subprocess.Popen
        children = []

        def launch(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            return child

        with mock.patch.object(app_logging.subprocess, 'Popen', side_effect=launch), \
                mock.patch.object(app_logging.selectors.KqueueSelector if sys.platform == 'darwin'
                                  else app_logging.selectors.EpollSelector,
                                  'select', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                app_logging.run_process([sys.executable, '-c', 'import time; time.sleep(30)'],
                                        task, tool='HandBrakeCLI')
        self.assertIsNotNone(children[0].returncode)

    def test_cleanup_covers_new_files_and_ignores_links(self):
        logs = self.root / 'logs'
        logs.mkdir()
        names = ['2026-01-01_01-01-01__diagnostic__process__abc.log',
                 '2026-01-01_01-01-01__fallback__update__abc.log']
        for name in names:
            path = logs / name
            path.write_text('old')
            os.utime(path, (1, 1))
        (logs / 'personal.log').write_text('keep')
        (logs / '2026-01-01_01-01-01__diagnostic__link.log').symlink_to(logs / 'personal.log')
        self.assertEqual(app_logging.clean_logs(self.root), 2)
        self.assertTrue((logs / 'personal.log').exists())

    def test_missing_compiler_is_optional(self):
        with mock.patch.object(app_logging.sys, 'platform', 'darwin'), \
                mock.patch.object(app_logging.subprocess, 'run', side_effect=FileNotFoundError):
            self.assertFalse(app_logging.build_native(self.root, self.root / 'source.c'))
        self.assertFalse((self.root / 'bin' / app_logging.BRIDGE_NAME).exists())

    def test_bridge_abi_mismatch_is_rejected(self):
        library = mock.Mock()
        library.clipfittt_log_abi_version.return_value = 2
        with mock.patch.object(ctypes, 'CDLL', return_value=library):
            with self.assertRaises(ValueError):
                REAL_NATIVE_BACKEND(self.root)

    def test_corrupt_version_file_does_not_block_logging(self):
        (self.root / 'VERSION').write_bytes(b'\xff\xfe')
        with app_logging.task_scope(self.root) as task:
            app_logging.event('input_ready', input_bytes=1)
        self.assertEqual(task.version, 'unknown')
        self.assertFalse(task.failed)


if __name__ == '__main__':
    unittest.main()
