# SPDX-License-Identifier: GPL-2.0-only
"""Check for HandBrakeCLI updates and install with explicit consent."""
from contextlib import contextmanager
import fcntl
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
    """Parse an upstream stable X.Y.Z version into comparable integers.

    This parser is for HandBrake releases, not ClipFitTT's beta version.
    Reject unsupported formats instead of guessing their update ordering.
    """
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError(f'无法识别稳定版本号：{value}')
    return tuple(map(int, match.groups()))


def binary_version(binary):
    """Read an executable's HandBrake version or raise a probe error."""
    result = subprocess.run([str(binary), '--version'], capture_output=True,
                            text=True, check=True, timeout=10)
    match = re.search(r'HandBrake\s+(\d+\.\d+\.\d+)', result.stdout + result.stderr)
    if not match:
        raise ValueError('无法读取 HandBrakeCLI 版本。')
    return match.group(1)


def request(url):
    """Build a versioned request without media or filesystem metadata."""
    project_version = Path(__file__).with_name('VERSION').read_text().strip()
    return urllib.request.Request(url, headers={
        'User-Agent': f'ClipFitTT/{project_version}', 'Accept': 'application/vnd.github+json'})


def latest_release():
    """Fetch and validate official stable macOS CLI release metadata.

    Return version, URL, and SHA-256 fields. Reject prereleases, unexpected
    download locations, or missing digests before an installer can use them.
    Network and metadata errors are handled by the caller.
    """
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
    # Do not substitute an unchecked download when release metadata changes.
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('官方发布信息缺少 SHA-256，无法自动校验安装包。')
    return dict(version=version, url=url, sha256=digest.split(':', 1)[1].lower())


def download(release, destination):
    """Download a new DMG and require its advertised SHA-256 to match.

    The destination must not already exist. Network reads have a timeout,
    and elapsed download time is checked between chunks. Partial files
    remain in the caller-owned temporary directory on failure.
    """
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
    """Check binary version, required flags, and the installed preset.

    Run only version/help/preset-export probes; no video is encoded. Any
    failed probe prevents the staged executable from replacing the old CLI.
    """
    if binary_version(binary) != version:
        raise ValueError('安装包中的 CLI 版本与发布信息不符。')
    result = subprocess.run([str(binary), '--help'], capture_output=True,
                            text=True, check=True, timeout=15)
    required = ('--multi-pass', '--crop-mode', '--no-metadata',
                '--no-keep-aname', '--no-keep-subname',
                '--automatic-naming-behaviour')
    if not all(flag in result.stdout + result.stderr for flag in required):
        raise ValueError('新版 CLI 不支持当前压缩参数，保持原版本。')
    # Exercise preset import/export without encoding a video.
    subprocess.run([str(binary), '--preset-import-file', str(root / 'preset.json'),
                    '-Z', 'ClipFitTT', '--preset-export', 'UpdateCompatibilityCheck'],
                   capture_output=True, check=True, timeout=15)


def install_release(release, binary, root):
    """Install a verified CLI, retaining notices and a previous-binary backup.

    Args:
        release: Validated metadata returned by latest_release().
        binary: Managed executable path; its parent must already exist.
        root: Installation root containing preset.json and local work space.

    Callers must obtain download consent and serialize access to the CLI.
    Validate before replacement, and restore the previous binary if the
    post-install version probe fails. Leave scratch files intact when an
    image cannot be unmounted safely.
    """
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
        # A sibling stage keeps executable replacement on one filesystem.
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
            # A first install has no backup; remove its rejected executable.
            if backup is not None:
                os.replace(backup, binary)
            else:
                binary.unlink(missing_ok=True)
            raise
        notices = root / 'third_party/HandBrake'
        # Preserve the upstream license material supplied with the DMG.
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


@contextmanager
def queue_lock(root, *, shared=False):
    """Protect CLI access with the installation's queue lock.

    Shared reads exclude installation and encoding's exclusive lock.
    Updates require an exclusive lock, including the version recheck.
    Release either lock on failure or cancellation. Never hold it during
    a prompt or the release-metadata request.
    """
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    with (root / '.queue.lock').open('a') as lock:
        try:
            fcntl.flock(lock, mode | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有压缩或安装任务，等待完成后检查或更新 HandBrakeCLI……', flush=True)
            fcntl.flock(lock, mode)
        yield


def check_for_updates(binary, root):
    """Check once, request consent, and return an update status.

    Return current, deferred, declined, updated, cancelled, or failed.
    Noninteractive runs never install. Report update failures without
    propagating them, so a completed video batch remains successful.
    Call without the queue lock: only local version probes and
    installation acquire it, leaving queued jobs free during user input.
    """
    print('\n正在检查 HandBrakeCLI 更新……', flush=True)
    try:
        with queue_lock(root, shared=True):
            current = binary_version(binary)
        release = latest_release()
        print(f'HandBrakeCLI：当前 {current}；官方稳定版 {release["version"]}', flush=True)
        if version_tuple(release['version']) <= version_tuple(current):
            print('HandBrakeCLI 无需更新。', flush=True)
            return 'current'
        if not sys.stdin.isatty():
            # Redirected input must never be treated as update approval.
            print('发现新版；当前为非交互运行，未执行更新。请从右键命令运行以确认更新。', flush=True)
            return 'deferred'
        answer = input(f'是否将 HandBrakeCLI 从 {current} 更新至 {release["version"]}？[y/N] ')
        if answer.strip().lower() not in ('y', 'yes'):
            print('已跳过更新，保留当前版本。', flush=True)
            return 'declined'
        with queue_lock(root):
            # Another batch may have installed this or a newer version
            # while this window waited for consent or the queue lock.
            current = binary_version(binary)
            if version_tuple(release['version']) <= version_tuple(current):
                print(f'HandBrakeCLI 已是 {current}，无需重复更新。', flush=True)
                return 'current'
            install_release(release, binary, root)
        return 'updated'
    except KeyboardInterrupt:
        print('\n已取消更新，已完成的视频不受影响。', flush=True)
        return 'cancelled'
    except Exception as error:
        print(f'HandBrakeCLI 更新检查或更新未完成：{error}。已完成的视频不受影响。',
              file=sys.stderr, flush=True)
        return 'failed'
