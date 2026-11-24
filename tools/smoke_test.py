#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise a real isolated installation and synthetic media on macOS."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    """Return fixture-command stdout or raise with a bounded stderr tail."""
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{Path(str(command[0])).name} failed:\n{result.stderr[-2000:]}')
    return result.stdout


def check_sharing_metadata(module, media, ffmpeg):
    """Verify tagged synthetic MOVs through all output paths.

    Use invented tags. Test rotation, SDR BT.709 color, frame count,
    source immutability, and audio language alongside metadata removal.
    Include an MP4-incompatible audio codec to exercise the AAC retry.
    """
    sentinels = ('PRIVATE_TEST_TITLE', 'PRIVATE_TEST_AUTHOR',
                 'PRIVATE_TEST_DEVICE', 'PRIVATE_TEST_VIDEO',
                 'PRIVATE_TEST_AUDIO', 'PRIVATE_TEST_TRACK',
                 '+12.3456+078.9012/', '2020-01-02T03:04:05')

    def video_hash(path):
        """Hash video packets without container tags or headers."""
        return run([ffmpeg, '-v', 'error', '-i', path, '-map', '0:v:0',
                    '-c:v', 'copy', '-f', 'hash', '-hash', 'sha256', '-'])

    for name, video_codec, audio_codec in (
            ('privacy-remux', 'libx264', 'aac'),
            ('privacy-transcode', 'mpeg4', 'aac'),
            ('privacy-audio-retry', 'libx264', 'pcm_alaw')):
        base, source = media / f'{name}-base.mov', media / f'{name}.mov'
        # Declare VUI; container-only color flags can be partial.
        vui = (['-x264-params',
                'colorprim=bt709:transfer=bt709:colormatrix=bt709:range=tv']
               if video_codec == 'libx264' else [])
        run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
             'testsrc2=size=320x180:rate=30000/1001', '-f', 'lavfi', '-i',
             'sine=frequency=440:sample_rate=48000', '-t', '2',
             '-c:v', video_codec, '-pix_fmt', 'yuv420p',
             '-color_primaries', 'bt709', '-color_trc', 'bt709',
             '-colorspace', 'bt709', '-color_range', 'tv', *vui,
             '-c:a', audio_codec, '-ac', '2', base])
        run([ffmpeg, '-v', 'error', '-display_rotation:v:0', '90', '-i', base,
             '-map', '0', '-c', 'copy', '-metadata', f'title={sentinels[0]}',
             '-metadata', f'artist={sentinels[1]}', '-metadata',
             f'com.apple.quicktime.model={sentinels[2]}', '-metadata',
             f'location={sentinels[6]}', '-metadata',
             f'creation_time={sentinels[7]}Z', '-metadata:s:v:0',
             f'handler_name={sentinels[3]}', '-metadata:s:a:0',
             f'handler_name={sentinels[4]}', '-metadata:s:a:0',
             f'title={sentinels[5]}', '-metadata:s:a:0', 'language=eng',
             '-movflags', 'use_metadata_tags', source])
        original_bytes = source.read_bytes()
        original = module.probe(source)
        original_tags = json.dumps([original['format_tags'],
                                    original['video'].get('tags', {}),
                                    original['audio'][0].get('tags', {})])
        assert all(value in original_tags for value in sentinels)
        assert original['dimensions'] == (180, 320)
        with contextlib.redirect_stdout(io.StringIO()) as console:
            output = module.compress(source, 1_500_000)
        result = module.verify(original, output, 1_500_000)
        output_tags = json.dumps([result['format_tags'],
                                 result['video'].get('tags', {}),
                                 result['audio'][0].get('tags', {})])
        assert not any(value in output_tags for value in sentinels)
        assert source.read_bytes() == original_bytes
        assert result['audio'][0]['tags']['language'] == 'eng'
        for key in ('color_space', 'color_transfer', 'color_primaries', 'color_range'):
            declared = original['video'].get(key)
            # Decoders can assign defaults for unspecified source colors.
            # Compare only values that the source explicitly declares.
            if declared not in (None, 'unknown', 'unspecified'):
                assert declared == result['video'].get(key), (name, key)
        if video_codec == 'libx264':
            assert original['video']['color_space'] == 'bt709'
            assert original['video']['color_transfer'] == 'bt709'
            assert original['video']['color_primaries'] == 'bt709'
            assert original['video']['color_range'] == 'tv'
            assert video_hash(source) == video_hash(output)
            assert result['video']['side_data_list'] == original['video']['side_data_list']
        if audio_codec == 'pcm_alaw':
            assert '重试仅将音频转换为 AAC' in console.getvalue()
            assert result['audio'][0]['codec_name'] == 'aac'


def check_unnamed_audio(module, media, ffmpeg):
    """Check unnamed stereo against strict sharing metadata rules.

    Unknown-language stereo without a source track name reproduced the
    regression. Test both timecoded and plain MP4 inputs, leaving the
    strict output metadata validator enabled throughout.
    """
    for timecoded in (False, True):
        source = media / f'unnamed-audio-{timecoded}.mp4'
        command = [ffmpeg, '-v', 'error', '-f', 'lavfi', '-i',
                   'testsrc2=size=320x180:rate=25', '-f', 'lavfi', '-i',
                   'sine=frequency=440:sample_rate=48000', '-t', '1',
                   '-c:v', 'mpeg4', '-c:a', 'aac', '-ac', '2',
                   '-metadata:s:a:0', 'language=und']
        if timecoded:
            command += ['-timecode', '00:00:00:00']
        run([*command, source])
        original = module.probe(source)
        assert original['audio'][0]['tags']['language'] == 'und'
        assert 'name' not in original['audio'][0].get('tags', {})
        with contextlib.redirect_stdout(io.StringIO()):
            output = module.compress(source, 1_000_000)
        result = module.verify(original, output, 1_000_000)
        tags = result['audio'][0]['tags']
        assert tags['handler_name'] == 'SoundHandler'
        assert 'name' not in tags


def main():
    """Exercise installation and media processing in temporary directories.

    Require an explicit local CLI, skip system service registration, and
    call per-file processing directly so the test makes no update request.
    All generated fixtures and the isolated installation are disposable.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handbrake', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='clipfittt-smoke-') as directory:
        directory = Path(directory)
        app, services = directory / 'App', directory / 'Services'
        installation = [sys.executable, ROOT / 'install.py', '--handbrake', args.handbrake,
                        '--app-dir', app, '--services-dir', services, '--no-register']
        run(installation)
        # Load the installed copy to detect packaging/dependency-path errors.
        spec = importlib.util.spec_from_file_location('installed_compress', app / 'compress.py')
        module = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(app))
        spec.loader.exec_module(module)
        ffmpeg = module.tool('ffmpeg')
        media = directory / 'media'
        media.mkdir()
        source, other = media / 'sample.mp4', media / 'other.mp4'
        common = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                  'testsrc2=size=320x180:rate=30000/1001', '-t', '2', '-an']
        run([*common, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', source])
        run([*common, '-c:v', 'mpeg4', '-q:v', '3', other])
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(module, 'probe', wraps=module.probe) as probes:
                remuxed = module.compress(source, 100_000_000)
                assert probes.call_count == 2
            with mock.patch.object(module, 'probe', wraps=module.probe) as probes, \
                    mock.patch.object(module, 'encode', wraps=module.encode) as encodes:
                transcoded = module.compress(other, 1_500_000)
                assert probes.call_count == 1 + encodes.call_count
        original_info = module.probe(source)
        remux_info = module.verify(original_info, remuxed, 100_000_000)
        module.verify(module.probe(other), transcoded, 1_500_000)
        def video_hash(path):
            """Hash copied video packets without container headers."""
            return run([ffmpeg, '-v', 'error', '-i', path, '-map', '0:v:0', '-c:v', 'copy',
                        '-f', 'hash', '-hash', 'sha256', '-']).strip()
        assert video_hash(source) == video_hash(remuxed)
        assert remux_info['video']['codec_name'] == 'h264'
        assert module.probe(other)['video']['codec_name'] != 'h264'
        check_sharing_metadata(module, media, ffmpeg)
        check_unnamed_audio(module, media, ffmpeg)
        # Reinstallation must retain recovery data and dependency selection.
        recovery = app / 'work' / 'keep.txt'
        recovery.write_text('recovery data')
        run(installation)
        assert recovery.read_text() == 'recovery data'
        config = json.loads((app / 'runtime_config.json').read_text())
        assert config['ffmpeg'] == ffmpeg
        workflows = list(services.glob('*.workflow'))
        assert len(workflows) == 1
        # Validate installed metadata, including the full prerelease identity.
        version = (app / 'VERSION').read_text().strip()
        with (workflows[0] / 'Contents/Info.plist').open('rb') as handle:
            info = plistlib.load(handle)
        assert info['CFBundleShortVersionString'] == version.partition('-')[0]
        assert info['ClipFitTTVersion'] == version
        assert module.cli_update.request(module.cli_update.RELEASE_API).get_header('User-agent') == f'ClipFitTT/{version}'
        assert not list((app / 'logs').glob('*__diagnostic__*.log'))
        if (app / 'bin' / module.app_logging.BRIDGE_NAME).exists():
            module.app_logging.NativeBackend(app)
            assert not list((app / 'logs').glob('*__fallback__process__*.log'))
        else:
            assert list((app / 'logs').glob('*__fallback__process__*.log'))
        print('PASS: isolated install/reinstall, runtime dependency paths, generated workflow,')
        print('H.264 remux packet identity, non-H.264 conversion, size/geometry/frame checks,')
        print('hybrid logging, recovery retention, and workflow/User-Agent version metadata.')
        print('Probe reuse: remux uses two probes; transcode uses one per attempt plus the source.')
        print('Sharing metadata: tagged MOV remux, transcode, AAC retry, rotation/color, and source retention.')
        print('Unnamed stereo: unknown language, with/without timecode, strict metadata checks passed.')
        print('No service registration or network request.')


if __name__ == '__main__':
    main()
