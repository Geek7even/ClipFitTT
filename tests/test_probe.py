# SPDX-License-Identifier: GPL-2.0-only
"""Cover probe timeouts, batch continuation, and encode recovery."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cli_update
import compress


class ProbeTests(unittest.TestCase):
    """Exercise probe failures without real media or networking."""

    def setUp(self):
        """Use temporary media, logs, and recovery files."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(compress, 'ROOT', self.root).start()
        mock.patch.object(compress, 'tool', return_value=sys.executable).start()
        self.console = io.StringIO()
        output = contextlib.redirect_stdout(self.console)
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        errors = contextlib.redirect_stderr(self.console)
        errors.__enter__()
        self.addCleanup(errors.__exit__, None, None, None)
        self.metadata = {
            'streams': [{'index': 0, 'codec_type': 'video', 'codec_name': 'h264',
                         'width': 320, 'height': 180, 'duration': '2',
                         'avg_frame_rate': '30/1', 'nb_frames': '60'}],
            'format': {'duration': '2'},
        }

    def test_normal_probe_preserves_rotation_and_stream_selection(self):
        self.metadata['streams'][0]['side_data_list'] = [{'rotation': 90}]
        self.metadata['streams'].insert(0, {
            'index': 1, 'codec_type': 'video', 'disposition': {'attached_pic': 1},
        })
        self.metadata['streams'].append({'index': 2, 'codec_type': 'audio'})
        result = subprocess.CompletedProcess([], 0, json.dumps(self.metadata), '')
        with mock.patch.object(compress.subprocess, 'run', return_value=result):
            info = compress.probe(self.root / 'sample.mov')
        self.assertEqual(info['video']['index'], 0)
        self.assertEqual(info['dimensions'], (180, 320))
        self.assertEqual(info['duration'], 2)
        self.assertEqual(len(info['audio']), 1)

    def test_timed_out_child_is_terminated_and_reaped(self):
        pid_file = self.root / 'probe.pid'
        real_run = subprocess.run

        def stalled_probe(command, **kwargs):
            # Emulate blocked inspection with a disposable Python child.
            # Keep run() real to exercise its timeout and child cleanup.
            script = ('import os, sys, time; from pathlib import Path; '
                      'Path(sys.argv[1]).write_text(str(os.getpid())); '
                      'time.sleep(30)')
            return real_run([sys.executable, '-c', script, str(pid_file)], **kwargs)

        started = time.monotonic()
        with mock.patch.object(compress, 'PROBE_TIMEOUT_SECONDS', 1), \
                mock.patch.object(compress.subprocess, 'run', side_effect=stalled_probe):
            with self.assertRaisesRegex(RuntimeError, '媒体信息探测超时（1 秒）') as caught:
                compress.probe(self.root / 'stalled.mov')
        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
        self.assertLess(time.monotonic() - started, 10)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_source_timeout_is_logged_and_next_file_is_processed(self):
        stalled = self.root / 'stalled.mov'
        normal = self.root / 'normal.mp4'
        for path in (stalled, normal):
            path.write_bytes(b'synthetic source')

        def inspect(command, **kwargs):
            if command[-1] == str(stalled):
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            return subprocess.CompletedProcess(command, 0, json.dumps(self.metadata), '')

        def remux(source, info, output, log):
            output.write_bytes(b'packed video')

        with mock.patch.object(compress.subprocess, 'run', side_effect=inspect), \
                mock.patch.object(compress, 'remux', side_effect=remux), \
                mock.patch.object(compress, 'verify'), \
                mock.patch.object(cli_update, 'check_for_updates') as updater, \
                mock.patch.object(sys, 'argv', ['clipfittt', str(stalled), str(normal)]):
            self.assertEqual(compress.main(), 1)
        self.assertFalse((self.root / 'stalled_compressed.mp4').exists())
        self.assertEqual((self.root / 'normal_compressed.mp4').read_bytes(), b'packed video')
        logs = list((self.root / 'logs').glob('*__process__*.log'))
        self.assertEqual(len(logs), 2)
        self.assertTrue(any('媒体信息探测超时（60 秒）' in path.read_text() for path in logs))
        self.assertTrue(any('保存校验通过' in path.read_text() for path in logs))
        updater.assert_called_once()

    def test_output_probe_timeout_keeps_completed_candidate(self):
        source = self.root / 'source.mov'
        source.write_bytes(b'synthetic source')
        self.metadata['streams'][0]['codec_name'] = 'mpeg4'

        def inspect(command, **kwargs):
            if command[-1] == str(source):
                return subprocess.CompletedProcess(command, 0, json.dumps(self.metadata), '')
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])

        def transcode(source, info, limit, working, log, *, probes=None):
            candidate = working / 'best.mp4'
            candidate.write_bytes(b'completed local encode')
            return candidate

        with mock.patch.object(compress.subprocess, 'run', side_effect=inspect), \
                mock.patch.object(compress, 'transcode', side_effect=transcode):
            with self.assertRaisesRegex(RuntimeError, '媒体信息探测超时'):
                compress.compress(source, 100_000_000)
        candidates = list((self.root / 'work').glob('encode-*/best.mp4'))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].read_bytes(), b'completed local encode')
        self.assertFalse((self.root / 'source_compressed.mp4').exists())
        log, = (self.root / 'logs').glob('*__process__*.log')
        self.assertIn('已保留编码结果', log.read_text())


if __name__ == '__main__':
    unittest.main()
