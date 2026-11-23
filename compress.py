#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare videos for mobile preview within decimal byte limits."""
import argparse
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from fractions import Fraction
from zoneinfo import ZoneInfo

import cli_update

ROOT = Path(__file__).resolve().parent
# Finder reports decimal MB; every successful output must be strictly smaller.
LIMIT = 100_000_000
# Aim below the ceiling to allow for bitrate rounding and container overhead.
TARGET_RATIO = 0.999
# Retention applies only to owned log names, including the legacy format.
LOG_PATTERN = re.compile(r'^(?:encode-[A-Za-z0-9_-]+|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}__(?:process|update)__[A-Za-z0-9_-]+)\.log$')


class Tee:
    """Mirror text to the original console and a task's open log stream."""

    def __init__(self, console, log):
        """Retain the caller-owned console and log streams."""
        self.console, self.log = console, log

    def write(self, text):
        """Write to both streams and flush diagnostics before returning."""
        self.console.write(text)
        self.log.write(text)
        self.log.flush()
        return len(text)

    def flush(self):
        """Flush both destinations without closing either stream."""
        self.console.flush()
        self.log.flush()

    def isatty(self):
        """Report the console's interactivity rather than the log file's."""
        return self.console.isatty()


def clean_logs(now=None):
    """Remove owned logs older than seven days and return their count.

    Args:
        now: Optional epoch timestamp for deterministic retention checks.

    Use modification time, ignore symlinks and unrelated filenames, and
    report individual deletion failures without stopping video processing.
    """
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    cutoff = (time.time() if now is None else now) - 7 * 24 * 60 * 60
    removed = 0
    for path in logs.iterdir():
        if not LOG_PATTERN.fullmatch(path.name) or path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as error:
            print(f'日志清理未完成：{path.name}：{error}', file=sys.stderr)
    return removed


@contextmanager
def task_log(kind='process'):
    """Yield a unique log path while mirroring console output into it.

    Names use Beijing time and omit source filenames. The temporary-file
    suffix prevents collisions between tasks started within the same second.
    """
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d_%H-%M-%S')
    fd, name = tempfile.mkstemp(prefix=f'{stamp}__{kind}__', suffix='.log', dir=logs)
    path = Path(name)
    with os.fdopen(fd, 'a', encoding='utf-8', buffering=1) as handle:
        with redirect_stdout(Tee(sys.stdout, handle)), redirect_stderr(Tee(sys.stderr, handle)):
            print(f'开始时间：{datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")}\n日志：{path}', flush=True)
            yield path


def tool(name):
    """Resolve an executable from installed configuration and fallbacks.

    HandBrakeCLI bypasses configured paths so its private, replaceable binary
    can be selected from this installation's bin directory.
    """
    configuration = ROOT / 'runtime_config.json'
    if configuration.is_file() and name != 'HandBrakeCLI':
        configured = json.loads(configuration.read_text()).get(name)
        if configured and Path(configured).is_file() and os.access(configured, os.X_OK):
            return configured
    for candidate in (ROOT / 'bin' / name, ROOT / 'vendor' / name,
                      Path('/opt/homebrew/bin') / name, Path('/usr/local/bin') / name):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f'找不到 {name}，请重新运行安装程序。')


def probe(path):
    """Return source metadata needed for routing and output validation.

    The result contains raw primary-video/all-audio stream dictionaries,
    a positive duration in seconds, and display-oriented pixel dimensions.
    Invalid media/probe results propagate to the per-file error handler.
    """
    result = subprocess.run([tool('ffprobe'), '-v', 'error', '-show_format',
                             '-show_streams', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    # Embedded cover art must not be mistaken for the actual video track.
    videos = [s for s in data['streams'] if s['codec_type'] == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    if not videos:
        raise RuntimeError('文件中没有视频轨。')
    video = videos[0]
    duration = float(video.get('duration') or data['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError('无法读取有效的视频时长。')
    rotation = next((float(s['rotation']) for s in video.get('side_data_list', [])
                     if 'rotation' in s), 0)
    width, height = video['width'], video['height']
    # A rotated MOV may store landscape pixels but display as portrait.
    if round(rotation) % 180:
        width, height = height, width
    return dict(video=video, duration=duration, dimensions=(width, height),
                audio=[s for s in data['streams'] if s['codec_type'] == 'audio'])


def verify(source, path, limit):
    """Validate an output against probe metadata and return its metadata.

    Args:
        source: Metadata returned by probe() for the original input.
        path: Completed local output to inspect before saving.
        limit: Exclusive upper size bound in decimal bytes.

    Raise RuntimeError for an unacceptable size, codec, geometry, duration,
    frame count/rate, or audio-track count. Probe/parse errors propagate.
    """
    output = probe(path)
    if not 0 < path.stat().st_size < limit:
        raise RuntimeError('输出文件未满足大小限制。')
    if output['video']['codec_name'] != 'h264':
        raise RuntimeError('输出编码不是 H.264。')
    if output['dimensions'] != source['dimensions']:
        raise RuntimeError(f"分辨率发生变化：{source['dimensions']} → {output['dimensions']}，不发布该输出。")
    a, b = source['video'], output['video']
    fps = float(Fraction(a.get('avg_frame_rate', '0/1')))
    # Allow short timestamp normalization, scaled for slow frame rates.
    if abs(output['duration'] - source['duration']) > max(0.15, 2 / max(fps, 1)):
        raise RuntimeError('输出视频时长发生变化，不发布该输出。')
    # Prefer exact frame counts; use average-rate tolerance if unavailable.
    if a.get('nb_frames', 'N/A') != 'N/A' and b.get('nb_frames', 'N/A') != 'N/A':
        if int(a['nb_frames']) != int(b['nb_frames']):
            raise RuntimeError('输出帧数发生变化，不发布该输出。')
    elif fps and abs(float(Fraction(b['avg_frame_rate'])) - fps) > max(0.01, fps * 0.001):
        raise RuntimeError('输出平均帧率发生变化，不发布该输出。')
    if len(output['audio']) != len(source['audio']):
        raise RuntimeError('输出音轨数量发生变化，不发布该输出。')
    return output


def publish(temp, source):
    """Copy verified media beside the source without overwriting a file.

    Reserve a numbered _compressed.mp4 name with exclusive creation, then
    verify the copy's size and SHA-256. On failure, attempt to remove that
    destination and leave the local input available for recovery. The new
    destination can be visible while copying; completion is not atomic.

    Return the final Path, or propagate the copy/validation exception.
    """
    # SMB/exFAT may not support hard links. Exclusive creation also works
    # across volumes and does not replace an existing destination.
    number = 1
    while True:
        suffix = '_compressed' if number == 1 else f'_compressed_{number}'
        output = source.with_name(source.stem + suffix + '.mp4')
        try:
            destination = output.open('xb')
        except FileExistsError:
            number += 1
            continue
        try:
            with destination, temp.open('rb') as original:
                shutil.copyfileobj(original, destination, length=1024 * 1024)
            if output.stat().st_size != temp.stat().st_size:
                raise OSError('保存后的文件大小不一致。')
            with temp.open('rb') as original, output.open('rb') as saved:
                if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(saved, 'sha256').digest():
                    raise OSError('保存后的文件校验不一致。')
            return output
        except BaseException:
            # Only remove the new destination that this invocation created.
            try:
                output.unlink()
            except OSError:
                print(f'未能清理未完成的输出，请检查：{output}', file=sys.stderr, flush=True)
            raise


@contextmanager
def encoding_workspace():
    """Yield local scratch space and retain best.mp4 after a failed task.

    Successful tasks and failures without a usable best candidate remove
    their scratch directory. The retained path is reported for recovery.
    """
    # Encode locally so a disconnected NAS cannot erase a completed encode.
    cache = ROOT / 'work'
    cache.mkdir(exist_ok=True)
    working = Path(tempfile.mkdtemp(prefix='encode-', dir=cache))
    try:
        yield working
    except BaseException:
        if (working / 'best.mp4').is_file():
            print(f'已保留编码结果，可直接复制到源目录，无需重新编码：{working / "best.mp4"}',
                  file=sys.stderr, flush=True)
        else:
            shutil.rmtree(working, ignore_errors=True)
        raise
    else:
        shutil.rmtree(working, ignore_errors=True)


def encode(command, log):
    """Run HandBrake, append diagnostics, and display encoding progress.

    Terminate the child when interrupted and raise RuntimeError for a
    nonzero exit status. The caller owns the candidate output and recovery.
    """
    with log.open('a', encoding='utf-8') as handle:
        handle.write('\n' + shlex.join(command) + '\n')
        handle.flush()
        # Stream stderr directly to disk so diagnostics cannot fill a pipe
        # while stdout is being consumed for progress updates.
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=handle,
                              text=True, bufsize=1) as process:
            try:
                for line in process.stdout:
                    # CLI progress uses CR, which universal-newline mode splits.
                    if 'Encoding:' in line:
                        print('\r' + line.strip()[:160].ljust(100), end='', flush=True)
                code = process.wait()
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        print()
    if code:
        raise RuntimeError(f'HandBrake 编码失败（{code}）。日志：{log}')


def remux(source, info, output, log):
    """Copy the primary video and all audio tracks into a workspace MP4.

    Retry a failed attempt with AAC audio when audio exists, while keeping
    video stream-copy enabled. Raise RuntimeError if remuxing still fails;
    the caller separately verifies the result and checks its final size.
    """
    # The candidate lives in scratch space; -y can replace it safely.
    command = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-loglevel', 'warning',
               '-y', '-copyts', '-start_at_zero', '-i', str(source),
               '-map', f'0:{info["video"]["index"]}', '-map', '0:a?',
               '-map_metadata', '0', '-map_chapters', '-1', '-c', 'copy',
               '-movflags', '+faststart', str(output)]
    for audio_retry in (False, True):
        if audio_retry:
            print('直接封装未成功，重试仅将音频转换为 AAC；视频继续直接复制。', flush=True)
            command = command[:-1] + ['-c:a', 'aac', '-b:a', '128k', command[-1]]
        with log.open('a', encoding='utf-8') as handle:
            handle.write('\n' + shlex.join(command) + '\n')
            handle.flush()
            with subprocess.Popen(command, stdout=handle, stderr=subprocess.STDOUT) as process:
                try:
                    code = process.wait()
                except BaseException:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    raise
        if code == 0:
            return
        if not info['audio']:
            break
    raise RuntimeError(f'FFmpeg 重新封装失败，未发布输出。日志：{log}')


def transcode(source, info, limit, working, log):
    """Return the best verified, under-limit candidate after up to four tries.

    Estimate an initial video bitrate from duration and the audio budget,
    then correct it using measured output sizes. Keep accepted candidates
    in working/best.mp4; never publish directly to the source directory.
    Raise RuntimeError if no attempt produces an acceptable output.
    """
    duration = info['duration']
    count = len(info['audio'])
    # Budget all audio tracks, MP4 tables, and packet overhead before video.
    total_kbps = limit * TARGET_RATIO * 8 / duration / 1000
    # Budget every selected audio track; units here are decimal kbit/s.
    audio_kbps = min(128, int(total_kbps * 0.20 / count)) if count else 0
    if count and audio_kbps < 16:
        raise RuntimeError('视频过长或音轨过多，100 MB 无法容纳最低音频预算。')
    video_kbps = int(total_kbps * 0.995 - audio_kbps * count)
    if video_kbps < 10:
        raise RuntimeError('视频过长，100 MB 无法容纳可编码的视频码率。')
    print(f'\n源文件：{source}\n时长：{duration:.3f} 秒；分辨率：{info["dimensions"]}；'
          f'目标：小于 {limit / 1e6:g} MB\n使用 HandBrake 同源帧率；H.264 两遍编码；音轨：{count}', flush=True)
    best = working / 'best.mp4'
    candidate = working / 'candidate.mp4'
    previous = None
    for attempt in range(1, 5):
        print(f'第 {attempt} 次编码：视频 {video_kbps} kbps，音频每轨 {audio_kbps} kbps', flush=True)
        command = [tool('HandBrakeCLI'), '--preset-import-file', str(ROOT / 'preset.json'),
                   '-Z', 'ClipFitTT', '-i', str(source), '-o', str(candidate),
                   '--min-duration', '0', '-f', 'av_mp4', '-e', 'x264',
                   '--encoder-preset', 'medium', '-b', str(video_kbps),
                   '--multi-pass', '--turbo', '--vfr', '--crop-mode', 'none',
                   '--crop', '0:0:0:0', '--no-comb-detect', '--no-decomb',
                   '--no-deinterlace', '--no-detelecine', '--no-markers',
                   '--optimize', '--subtitle', 'none']
        if count:
            command += ['--all-audio', '-E', 'ca_aac', '-B', str(audio_kbps),
                        '--mixdown', 'stereo']
        else:
            command += ['-a', 'none']
        encode(command, log)
        actual = candidate.stat().st_size
        print(f'实际大小：{actual / 1e6:.3f} MB', flush=True)
        if actual < limit:
            verify(info, candidate, limit)
            # A later attempt can be smaller; retain the largest valid one.
            if not best.exists() or actual > best.stat().st_size:
                os.replace(candidate, best)
            if actual >= limit * 0.99:
                break
            if previous and previous[1] < limit and actual <= previous[1] * 1.002:
                # Stop retrying when another valid attempt adds little size.
                print('编码器已接近该内容的有效码率上限，保留较小输出。')
                break
        # The candidate may already have been promoted to best.mp4.
        output_info = probe(candidate if candidate.exists() else best)
        audio_bytes = sum(float(s.get('bit_rate', audio_kbps * 1000)) *
                          float(s.get('duration', duration)) / 8 for s in output_info['audio'])
        overhead = max(10_000, actual * 0.002)
        # Correct the video budget after subtracting estimated audio/tables.
        factor = ((limit * TARGET_RATIO - audio_bytes - overhead) /
                  max(1, actual - audio_bytes - overhead))
        new_rate = max(1, int(video_kbps * min(factor, 2.0)))
        if actual >= limit:
            # Force a decrease even when integer rounding masks overshoot.
            new_rate = min(video_kbps - 1, int(new_rate * 0.995))
        if new_rate == video_kbps:
            break
        previous = (video_kbps, actual)
        video_kbps = new_rate
        candidate.unlink(missing_ok=True)
    if not best.exists():
        raise RuntimeError(f'多次编码后仍超过大小上限，未生成最终文件。日志：{log}')
    return best


def process_video(source, limit, log):
    """Route, validate, and save one input, returning the output Path.

    Small H.264 files use remuxing. Equality with the size ceiling, another
    codec, or an oversized remux result selects HandBrake transcoding.
    Only validated local media is handed to publish().
    """
    source = Path(source).absolute()
    if not source.is_file() or source.suffix.lower() not in ('.mp4', '.mov'):
        raise RuntimeError('仅支持 MP4 / MOV 文件。')
    info = probe(source)
    size = source.stat().st_size
    codec = info['video']['codec_name']
    print(f'源文件：{source}\n源大小：{size / 1e6:.3f} MB；视频编码：{codec}', flush=True)
    with encoding_workspace() as working:
        best = None
        if size < limit and codec == 'h264':
            print('处理方式：重新封装；源文件小于上限且为 H.264，跳过视频转码。', flush=True)
            packed = working / 'remux.mp4'
            remux(source, info, packed, log)
            actual = packed.stat().st_size
            print(f'重新封装大小：{actual / 1e6:.3f} MB', flush=True)
            if actual < limit:
                verify(info, packed, limit)
                best = working / 'best.mp4'
                os.replace(packed, best)
            else:
                print('重新封装达到或超过大小上限，自动转入 HandBrake 转码。', flush=True)
                packed.unlink()
        if best is None:
            print('处理方式：HandBrake 限大小转码。', flush=True)
            best = transcode(source, info, limit, working, log)
        verify(info, best, limit)
        print('正在保存到源目录并校验文件……', flush=True)
        result = publish(best, source)
        print('保存校验通过：文件大小和 SHA-256 一致。', flush=True)
        print(f'完成：{result}\n大小：{result.stat().st_size / 1e6:.3f} MB\n日志：{log}', flush=True)
        return result


def compress(source, limit):
    """Clean expired logs and process a file inside a tee-backed task log.

    Record failures, including cancellation, before propagating them to the
    batch handler. Workspace cleanup/recovery is managed independently.
    """
    removed = clean_logs()
    with task_log() as log:
        print(f'已清理超过 7 天的日志：{removed} 份。', flush=True)
        try:
            return process_video(source, limit, log)
        except BaseException as error:
            print(f'处理失败：{source}\n{type(error).__name__}：{error}', file=sys.stderr, flush=True)
            if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                print(error.stderr, file=sys.stderr, flush=True)
            raise


def launch(files):
    """Open a self-cleaning Terminal command for Finder-selected paths.

    Quote every argument independently, and use -- to separate paths from
    CLI options. Encoding runs outside Automator's short-lived service job.
    """
    # Open a .command document, avoiding Terminal Apple Events permission.
    fd, filename = tempfile.mkstemp(prefix='ClipFitTT-', suffix='.command')
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write('#!/bin/zsh\n')
        handle.write('/bin/rm -f -- "$0"\n')
        handle.write(shlex.join([sys.executable, '-u', str(Path(__file__).resolve()), '--', *files]) + '\n')
        handle.write('result=$?\nprintf "\\n处理结束，按回车关闭此窗口。"\nread reply\nexit "$result"\n')
    os.chmod(filename, 0o700)
    subprocess.run(['/usr/bin/open', '-a', 'Terminal', filename], check=True)


def main():
    """Run a serialized batch and return a nonzero status if any file fails.

    The installation-wide lock also covers the optional CLI update check,
    preventing this tool's queued tasks from using a binary being replaced.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launch', action='store_true')
    parser.add_argument('--limit-bytes', type=int, default=LIMIT, help=argparse.SUPPRESS)
    parser.add_argument('files', nargs='+')
    args = parser.parse_args()
    if args.launch:
        launch(args.files)
        return 0
    if args.limit_bytes <= 0:
        parser.error('size limit must be positive')
    failures = 0
    with (ROOT / '.queue.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有压缩任务，正在排队等待……', flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        for filename in dict.fromkeys(args.files):
            try:
                compress(filename, args.limit_bytes)
            except (RuntimeError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
                failures += 1
                print(f'\n失败：{filename}\n{error}', file=sys.stderr, flush=True)
        with task_log('update'):
            try:
                cli_update.check_for_updates(Path(tool('HandBrakeCLI')), ROOT)
            except (OSError, RuntimeError) as error:
                print(f'HandBrakeCLI 更新检查未完成：{error}。已完成的视频不受影响。', file=sys.stderr)
    print(f'\n全部处理结束，失败 {failures} 个。', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已取消，源文件保持不变。', file=sys.stderr)
        sys.exit(130)
