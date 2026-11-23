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

ROOT = Path(__file__).resolve().parents[1]


def run(command):
    """Return fixture-command stdout or raise with a bounded stderr tail."""
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{Path(str(command[0])).name} failed:\n{result.stderr[-2000:]}')
    return result.stdout


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
            remuxed = module.compress(source, 100_000_000)
            transcoded = module.compress(other, 1_500_000)
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
        assert list((app / 'logs').glob('*__process__*.log'))
        print('PASS: isolated install/reinstall, runtime dependency paths, generated workflow,')
        print('H.264 remux packet identity, non-H.264 conversion, size/geometry/frame checks,')
        print('timestamped logs, recovery retention, and workflow/User-Agent version metadata.')
        print('No service registration or network request.')


if __name__ == '__main__':
    main()
