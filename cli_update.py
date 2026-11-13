# SPDX-License-Identifier: GPL-2.0-only
"""Check and, only with interactive consent, update the private HandBrakeCLI."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

RELEASE_API = 'https://api.github.com/repos/HandBrake/HandBrake/releases/latest'


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError(f'无法识别稳定版本号：{value}')
    return tuple(map(int, match.groups()))


def binary_version(binary):
    result = subprocess.run([str(binary), '--version'], capture_output=True,
                            text=True, check=True, timeout=10)
    match = re.search(r'HandBrake\s+(\d+\.\d+\.\d+)', result.stdout + result.stderr)
    if not match:
        raise ValueError('无法读取 HandBrakeCLI 版本。')
    return match.group(1)


def request(url):
    return urllib.request.Request(url, headers={
        'User-Agent': 'FinderVideoCompressor/1.0.0', 'Accept': 'application/vnd.github+json'})


def latest_release():
    with urllib.request.urlopen(request(RELEASE_API), timeout=5) as response:
        data = json.loads(response.read(2_000_000))
    if data.get('draft') or data.get('prerelease'):
        raise ValueError('官方版本信息不是稳定发布。')
    tag = data['tag_name']
    version_tuple(tag)
    version = tag.removeprefix('v')
    name = f'HandBrakeCLI-{version}.dmg'
    asset = next((a for a in data['assets'] if a['name'] == name), None)
    if asset is None:
        raise ValueError('官方发布中没有 macOS HandBrakeCLI 安装包。')
    url = asset['browser_download_url']
    expected_url = f'https://github.com/HandBrake/HandBrake/releases/download/{tag}/{name}'
    if url != expected_url:
        raise ValueError('安装包地址不是 HandBrake 官方发布地址。')
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('官方发布信息缺少 SHA-256，无法自动校验安装包。')
    return dict(version=version, url=url, sha256=digest.split(':', 1)[1].lower())


def download(release, destination):
    started = time.monotonic()
    with urllib.request.urlopen(request(release['url']), timeout=20) as response:
        with destination.open('xb') as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                if time.monotonic() - started > 180:
                    raise TimeoutError('下载 HandBrakeCLI 超时。')
    with destination.open('rb') as handle:
        digest = hashlib.file_digest(handle, 'sha256').hexdigest()
    if digest != release['sha256']:
        raise ValueError('HandBrakeCLI 安装包 SHA-256 校验失败，已取消更新。')


def validate_candidate(binary, version, root):
    if binary_version(binary) != version:
        raise ValueError('安装包中的 CLI 版本与发布信息不符。')
    result = subprocess.run([str(binary), '--help'], capture_output=True,
                            text=True, check=True, timeout=15)
    if not all(flag in result.stdout + result.stderr for flag in ('--multi-pass', '--crop-mode')):
        raise ValueError('新版 CLI 不支持当前压缩参数，保持原版本。')
    # Importing/exporting exercises the current preset without encoding a video.
    subprocess.run([str(binary), '--preset-import-file', str(root / 'preset.json'),
                    '-Z', 'Compressor', '--preset-export', 'UpdateCompatibilityCheck'],
                   capture_output=True, check=True, timeout=15)


def install_release(release, binary, root):
    work = root / 'work'
    work.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='cli-update-', dir=work))
    image = directory / 'HandBrakeCLI.dmg'
    mount = directory / 'mount'
    mount.mkdir()
    # Mark mounted before invoking hdiutil so even a timed-out attach gets a
    # detach attempt. Never recursively clean a possibly mounted filesystem.
    mounted = False
    stage = None
    try:
        print(f'正在下载 HandBrakeCLI {release["version"]} 并校验安装包……', flush=True)
        download(release, image)
        mounted = True
        subprocess.run(['/usr/bin/hdiutil', 'attach', '-nobrowse', '-readonly',
                        '-mountpoint', str(mount), str(image)],
                       capture_output=True, check=True, timeout=45)
        source = mount / 'HandBrakeCLI'
        if not source.is_file():
            raise ValueError('官方安装包中没有 HandBrakeCLI。')
        fd, staged = tempfile.mkstemp(prefix='.HandBrakeCLI-', dir=binary.parent)
        os.close(fd)
        stage = Path(staged)
        shutil.copyfile(source, stage)
        stage.chmod(0o755)
        validate_candidate(stage, release['version'], root)
        # Keep a complete backup before replacing the active executable.
        backup = None
        if binary.exists():
            backup = binary.with_name(binary.name + '.previous')
            backup_stage = directory / 'previous'
            shutil.copy2(binary, backup_stage)
            os.replace(backup_stage, backup)
        os.replace(stage, binary)
        stage = None
        try:
            if binary_version(binary) != release['version']:
                raise ValueError('安装后的 HandBrakeCLI 版本校验失败。')
        except BaseException:
            if backup is not None:
                os.replace(backup, binary)
            else:
                binary.unlink(missing_ok=True)
            raise
        notices = root / 'third_party/HandBrake'
        notices.mkdir(parents=True, exist_ok=True)
        for name in ('LICENSE', 'COPYING', 'THANKS.markdown', 'AUTHORS.markdown'):
            source_notice = mount / 'doc' / name
            if source_notice.is_file():
                shutil.copy2(source_notice, notices / name)
        (notices / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
        print(f'HandBrakeCLI 已安装为 {release["version"]}' +
              (f'；旧版备份：{backup}' if backup else '；首次安装'), flush=True)
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)
        if mounted:
            try:
                subprocess.run(['/usr/bin/hdiutil', 'detach', str(mount)],
                               capture_output=True, check=True, timeout=30)
                mounted = False
            except (OSError, subprocess.SubprocessError) as error:
                print(f'安装镜像尚未卸载：{mount}；{error}', file=sys.stderr, flush=True)
        if not mounted:
            shutil.rmtree(directory, ignore_errors=True)


def check_for_updates(binary, root):
    print('\n正在检查 HandBrakeCLI 更新……', flush=True)
    try:
        current = binary_version(binary)
        release = latest_release()
        print(f'HandBrakeCLI：当前 {current}；官方稳定版 {release["version"]}', flush=True)
        if version_tuple(release['version']) <= version_tuple(current):
            print('HandBrakeCLI 无需更新。', flush=True)
            return 'current'
        if not sys.stdin.isatty():
            print('发现新版；当前为非交互运行，未执行更新。请从右键命令运行以确认更新。', flush=True)
            return 'deferred'
        answer = input(f'是否将 HandBrakeCLI 从 {current} 更新至 {release["version"]}？[y/N] ')
        if answer.strip().lower() not in ('y', 'yes'):
            print('已跳过更新，保留当前版本。', flush=True)
            return 'declined'
        install_release(release, binary, root)
        return 'updated'
    except KeyboardInterrupt:
        print('\n已取消更新，已完成的视频不受影响。', flush=True)
        return 'cancelled'
    except Exception as error:
        print(f'HandBrakeCLI 更新检查或更新未完成：{error}。已完成的视频不受影响。',
              file=sys.stderr, flush=True)
        return 'failed'
