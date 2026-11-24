#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install ClipFitTT's Finder service for the current user."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import app_logging
import build_service
import cli_update

ROOT = Path(__file__).resolve().parent
MINIMUM_CLI = '1.11.2'
# Explicit ownership keeps reinstalls from replacing logs or recovery media.
MANAGED_FILES = ('app_logging.py', 'oslog_bridge.c', 'cli_update.py', 'compress.py',
                 'preset.json', 'README.md', 'DESCRIPTION.txt',
                 'LICENSE', 'COPYRIGHT', 'THIRD_PARTY_NOTICES.md', 'PRIVACY.md',
                 'CONTRIBUTING.md', 'RELEASING.md', 'CHANGELOG.md', 'VERSION')


def find_executable(name):
    """Locate an executable on PATH or common Apple Silicon/Intel prefixes."""
    found = shutil.which(name)
    if found:
        return Path(found).resolve()
    for prefix in ('/opt/homebrew/bin', '/usr/local/bin'):
        candidate = Path(prefix) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise RuntimeError(f'找不到 {name}。请先安装 FFmpeg，并确认 ffmpeg 和 ffprobe 可执行。')


def preflight():
    """Validate platform/tools and return resolved FFmpeg/ffprobe paths.

    Check existing dependencies only; do not install a package manager,
    Python, or FFmpeg as a side effect.
    """
    if sys.platform != 'darwin':
        raise RuntimeError('Finder 右键服务仅支持 macOS。')
    if sys.version_info < (3, 11):
        raise RuntimeError('需要 Python 3.11 或更高版本。')
    tools = {name: str(find_executable(name)) for name in ('ffmpeg', 'ffprobe')}
    for name, binary in tools.items():
        subprocess.run([binary, '-version'], capture_output=True, check=True, timeout=10)
    return tools


def choose_cli(explicit, destination):
    """Return a compatible existing CLI Path, or None if none is usable.

    An explicit path must pass every check. Automatic discovery prefers the
    installed private CLI, preserving a compatible newer version on reinstall.
    This function does not access the network or authorize a download.
    """
    candidates = [Path(explicit).expanduser()] if explicit else [destination / 'bin/HandBrakeCLI']
    if not explicit:
        found = shutil.which('HandBrakeCLI')
        if found:
            candidates.append(Path(found))
        candidates.extend(Path(prefix) / 'HandBrakeCLI' for prefix in ('/opt/homebrew/bin', '/usr/local/bin'))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            try:
                version = cli_update.binary_version(candidate)
                if cli_update.version_tuple(version) < cli_update.version_tuple(MINIMUM_CLI):
                    raise ValueError(f'需要 HandBrakeCLI >= {MINIMUM_CLI}，找到 {version}')
                cli_update.validate_candidate(candidate, version, ROOT)
                return candidate.resolve()
            except (OSError, ValueError, subprocess.SubprocessError):
                if explicit:
                    raise
    if explicit:
        raise RuntimeError('--handbrake 指定的路径不是可用的 HandBrakeCLI。')
    return None


def commit_install(stage, workflow, destination, services):
    """Install staged managed files and a service with rollback on failure.

    Args:
        stage: Prepared package root including a compatible private CLI.
        workflow: Generated service directory targeting the final app path.
        destination: Application-support directory to update in place.
        services: User Services directory, or an isolated test equivalent.

    Replace files individually on their target filesystem. Keep prior files
    and the prior workflow until replacement succeeds, restoring them after
    a later failure. Leave unowned logs and recovery directories untouched.
    The caller must hold the installation's queue lock.
    """
    destination.mkdir(parents=True, exist_ok=True)
    services.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.install-backup-', dir=destination.parent) as saved:
        saved = Path(saved)
        entries = [Path(name) for name in (*MANAGED_FILES, 'runtime_config.json', 'bin/HandBrakeCLI')]
        if (stage / 'bin' / app_logging.BRIDGE_NAME).is_file():
            entries.append(Path('bin') / app_logging.BRIDGE_NAME)
        if (stage / 'third_party').exists():
            entries.extend(path.relative_to(stage) for path in (stage / 'third_party').rglob('*') if path.is_file())
        changed = []
        target_workflow = services / workflow.name
        old_workflow = services / ('.previous-' + workflow.name)
        # The service may live on a different volume from application data.
        service_staged = Path(tempfile.mkdtemp(prefix='.service-install-', dir=services))
        service_saved = False
        service_installed = False
        try:
            if old_workflow.exists():
                # Preserve a prior interrupted install's recovery material.
                raise RuntimeError(f'发现上次安装的恢复目录，请先检查：{old_workflow}')
            shutil.copytree(workflow, service_staged / workflow.name)
            for relative in entries:
                target = destination / relative
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise RuntimeError(f'安装目标不是普通文件：{target}')
                if target.exists():
                    backup = saved / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                target.parent.mkdir(parents=True, exist_ok=True)
                # Stage on the target filesystem before atomic replacement.
                fd, filename = tempfile.mkstemp(prefix='.install-', dir=target.parent)
                os.close(fd)
                try:
                    shutil.copy2(stage / relative, filename)
                    os.replace(filename, target)
                finally:
                    Path(filename).unlink(missing_ok=True)
                changed.append(relative)
            if target_workflow.exists():
                if target_workflow.is_symlink():
                    raise RuntimeError('现有 Finder 工作流为符号链接，已取消安装。')
                os.replace(target_workflow, old_workflow)
                service_saved = True
            os.replace(service_staged / workflow.name, target_workflow)
            service_installed = True
        except BaseException:
            # Undo the service change first, then restore files in reverse.
            if service_installed:
                shutil.rmtree(target_workflow)
            if service_saved:
                os.replace(old_workflow, target_workflow)
            for relative in reversed(changed):
                backup = saved / relative
                if backup.exists():
                    shutil.copy2(backup, destination / relative)
                else:
                    (destination / relative).unlink(missing_ok=True)
            raise
        else:
            if service_saved:
                shutil.rmtree(old_workflow)
        finally:
            shutil.rmtree(service_staged, ignore_errors=True)


def install(args):
    """Prepare, validate, and install a package from parsed CLI options.

    Resolve dependencies before changing managed files. Reuse an existing
    CLI, or obtain download consent before provisioning an official build.
    Hold the queue lock through staging and replacement, then refresh the
    service registry unless isolated installation was requested.
    """
    tools = preflight()
    destination = Path(args.app_dir).expanduser().absolute()
    services = Path(args.services_dir).expanduser().absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=True)
    with app_logging.task_scope(destination, 'install',
                                version=(ROOT / 'VERSION').read_text().strip()):
        with (destination / '.queue.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            local_cli = choose_cli(args.handbrake, destination)
            allow_download = args.download_cli
            if local_cli is None and not allow_download:
                if sys.stdin.isatty():
                    allow_download = input('未找到可用 CLI。从 HandBrake 官方发布下载并校验？[y/N] ').strip().lower() in ('y', 'yes')
                if not allow_download:
                    raise RuntimeError('请安装 HandBrakeCLI，使用 --handbrake 指定路径，或明确添加 --download-cli。')
            with tempfile.TemporaryDirectory(prefix='.clipfittt-install-', dir=destination.parent) as directory:
                stage = Path(directory)
                (stage / 'bin').mkdir()
                for name in MANAGED_FILES:
                    shutil.copy2(ROOT / name, stage / name)
                native_logging = app_logging.build_native(stage, ROOT / 'oslog_bridge.c')
                app_logging.event('logging_backend_prepared', category='install',
                                  status='native' if native_logging else 'fallback')
                print('统一日志桥接已准备。' if native_logging else
                      '统一日志桥接暂不可用，继续使用文件日志回退。', flush=True)
                if local_cli:
                    shutil.copy2(local_cli, stage / 'bin/HandBrakeCLI')
                else:
                    cli_update.install_release(cli_update.latest_release(), stage / 'bin/HandBrakeCLI', stage)
                # Store executable paths locally, outside the published package.
                (stage / 'runtime_config.json').write_text(json.dumps(tools, indent=2) + '\n')
                workflow = build_service.build(stage / 'generated', destination, sys.executable)
                commit_install(stage, workflow, destination, services)
        if not args.no_register:
            try:
                subprocess.run(['/System/Library/CoreServices/pbs', '-update'], check=True, timeout=15)
            except (OSError, subprocess.SubprocessError) as error:
                # Registry refresh is separate from the completed file install.
                print(f'文件已安装，系统服务刷新未完成：{error}。请注销后重新登录以刷新菜单。', file=sys.stderr)
        version = (ROOT / 'VERSION').read_text().strip()
        print(f'{build_service.display_version(version)}（{version}）')
        print(f'已安装：{services / (build_service.NAME + ".workflow")}\n程序：{destination}')
        print('安装路径仅保存在本机生成文件中；无需 sudo。')


def main():
    """Parse options and exit with code 1 on installation failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--handbrake', metavar='PATH', help='使用已有的 HandBrakeCLI')
    parser.add_argument('--download-cli', action='store_true', help='没有可用 CLI 时允许从官方下载')
    parser.add_argument('--app-dir', default=str(build_service.DEFAULT_APP_DIR))
    parser.add_argument('--services-dir', default=str(Path.home() / 'Library/Services'))
    parser.add_argument('--no-register', action='store_true', help='隔离测试时跳过系统服务注册')
    args = parser.parse_args()
    try:
        install(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f'安装失败：{error}\n')


if __name__ == '__main__':
    main()
