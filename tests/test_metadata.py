# SPDX-License-Identifier: GPL-2.0-only
"""Enforce sharing metadata rules using synthetic test data."""
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


class MetadataTests(unittest.TestCase):
    """Reject personal tags and allow playback data."""

    def setUp(self):
        """Provide generic muxer tags and playback fields."""
        self.info = dict(format_tags={'major_brand': 'isom', 'encoder': 'Lavf'},
                         video={'tags': {'language': 'und', 'handler_name': 'VideoHandler'},
                                'color_space': 'bt709',
                                'side_data_list': [{'rotation': 90}]},
                         audio=[{'tags': {'language': 'eng', 'handler_name': 'SoundHandler'}}])

    def test_technical_tags_rotation_and_color_are_allowed(self):
        compress.verify_metadata(self.info)
        self.assertEqual(self.info['video']['side_data_list'][0]['rotation'], 90)
        self.assertEqual(self.info['video']['color_space'], 'bt709')

    def test_descriptive_container_tags_are_rejected(self):
        for key in ('title', 'artist', 'comment', 'location',
                    'com.apple.quicktime.model', 'custom_private_field'):
            with self.subTest(key=key):
                info = {**self.info, 'format_tags': {key: 'synthetic private tag'}}
                with self.assertRaisesRegex(RuntimeError, '描述性元数据'):
                    compress.verify_metadata(info)

    def test_source_track_descriptions_are_rejected(self):
        for key in ('title', 'name', 'comment', 'encoder'):
            with self.subTest(key=key):
                self.info['audio'][0]['tags'][key] = 'synthetic track name'
                with self.assertRaisesRegex(RuntimeError, '轨道元数据'):
                    compress.verify_metadata(self.info)
                del self.info['audio'][0]['tags'][key]

    def test_custom_handler_and_non_language_text_are_rejected(self):
        for key, value in (('handler_name', 'synthetic camera owner'),
                           ('language', 'synthetic track description')):
            with self.subTest(key=key):
                with mock.patch.dict(self.info['video']['tags'], {key: value}):
                    with self.assertRaisesRegex(RuntimeError, '轨道元数据'):
                        compress.verify_metadata(self.info)

    def test_new_export_timestamp_is_allowed(self):
        self.info['format_tags']['creation_time'] = '2026-01-01T00:00:00Z'
        self.info['video']['tags']['creation_time'] = '2026-01-01T00:00:00Z'
        compress.verify_metadata(self.info)

    def test_generated_layout_name_still_fails_strict_metadata_policy(self):
        self.info['audio'][0]['tags'].update(name='Stereo', handler_name='Stereo')
        with self.assertRaisesRegex(RuntimeError, '轨道元数据'):
            compress.verify_metadata(self.info)

    def test_cli_without_privacy_flags_is_rejected_before_preset_probe(self):
        flags = ['--multi-pass', '--crop-mode', '--no-metadata',
                 '--no-keep-aname', '--no-keep-subname',
                 '--automatic-naming-behaviour']
        for missing in flags[2:]:
            with self.subTest(missing=missing):
                help_text = ' '.join(flag for flag in flags if flag != missing)
                with mock.patch.object(cli_update, 'binary_version', return_value='1.12.0'), \
                        mock.patch.object(cli_update.subprocess, 'run', return_value=
                                          subprocess.CompletedProcess([], 0, help_text, '')) as run:
                    with self.assertRaisesRegex(ValueError, '不支持当前压缩参数'):
                        cli_update.validate_candidate(Path('CLI'), '1.12.0', Path('.'))
                self.assertEqual(run.call_count, 1)

    def test_unexpected_output_tags_prevent_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.mov'
            source.write_bytes(b'original source')
            info = dict(video={'codec_name': 'h264', 'index': 0,
                               'avg_frame_rate': '30/1', 'nb_frames': '60'},
                        duration=2, dimensions=(320, 180), audio=[])
            tagged = {**info, 'format_tags': {'title': 'synthetic private title'}}

            def remux(source, info, output, log):
                output.write_bytes(b'packed video')

            with mock.patch.object(compress, 'ROOT', root), \
                    mock.patch.object(compress, 'probe', side_effect=[info, tagged]), \
                    mock.patch.object(compress, 'remux', side_effect=remux), \
                    mock.patch.object(compress, 'publish') as publish, \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, '描述性元数据'):
                    compress.compress(source, 100_000_000)
            publish.assert_not_called()
            self.assertEqual(source.read_bytes(), b'original source')


if __name__ == '__main__':
    unittest.main()
