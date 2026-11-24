#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Prepare videos for mobile preview within decimal byte limits."""
import argparse
from contextlib import contextmanager
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

import app_logging
import cli_update

ROOT = Path(__file__).resolve().parent
# Finder reports decimal MB; every successful output must be strictly smaller.
LIMIT = 100_000_000
# Allow for bitrate rounding and container overhead below the ceiling.
TARGET_RATIO = 0.999
# Bound metadata inspection without limiting the encoding duration.
PROBE_TIMEOUT_SECONDS = 60
# Permit muxer/playback tags, not source descriptions or track names.
FORMAT_TAGS = frozenset({'major_brand', 'minor_version', 'compatible_brands',
                         'encoder', 'creation_time'})
STREAM_TAGS = frozenset({'language', 'handler_name', 'creation_time'})
# Retain the cleanup entry point for legacy logs and local diagnostics.
LOG_PATTERN = app_logging.LOG_PATTERN


def clean_logs(now=None):
    """Prune owned diagnostic/legacy files without failing a media task."""
    return app_logging.clean_logs(ROOT, now)


@contextmanager
def task_log(kind='process'):
    """Yield a correlated task with bounded, failure-only raw diagnostics."""
    with app_logging.task_scope(ROOT, kind) as task:
        yield task


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

    Return primary-video/all-audio stream dictionaries, a positive
    duration in seconds, and display-oriented pixel dimensions.
    Include container tags for the final sharing-policy check.
    Raise RuntimeError after a probe timeout; subprocess.run handles
    child cleanup. The deadline applies only to ffprobe, not other file
    I/O or encoding. Other probe/parse errors propagate to the per-file
    error handler, allowing the batch to continue.
    """
    started = time.monotonic()
    app_logging.event('probe_started', category='probe', level='info')
    try:
        result = subprocess.run([tool('ffprobe'), '-v', 'error', '-show_format',
                                 '-show_streams', '-of', 'json', str(path)],
                                capture_output=True, text=True, check=True,
                                timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        app_logging.event('probe_failed', category='probe', level='error',
                          reason='timeout', private_detail=str(path))
        # Reuse batch error handling and best.mp4 recovery for
        # timeouts during either source or output inspection.
        raise RuntimeError(
            f'媒体信息探测超时（{PROBE_TIMEOUT_SECONDS} 秒）：{path}。'
            '请检查网络、NAS 挂载或源文件，确认后重试。'
        ) from error
    except (OSError, subprocess.SubprocessError) as error:
        app_logging.event('probe_failed', category='probe', level='error',
                          reason='nonzero_exit' if isinstance(
                              error, subprocess.CalledProcessError) else 'io_error',
                          returncode=getattr(error, 'returncode', 0),
                          private_detail=str(error))
        raise
    data = json.loads(result.stdout)
    # Exclude embedded cover art from the actual video tracks.
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
    app_logging.event('probe_completed', category='probe', level='info',
                      duration_ms=round((time.monotonic() - started) * 1000))
    return dict(video=video, duration=duration, dimensions=(width, height),
                format_tags=data.get('format', {}).get('tags', {}),
                audio=[s for s in data['streams'] if s['codec_type'] == 'audio'])


class ProbeCache:
    """Reuse metadata in one private encode workspace.

    Create one instance per input video. Track file identity, size, and
    modification/change times so rewriting candidate.mp4 invalidates its
    entry. Never share this cache across jobs or use it for NAS sources.
    """

    def __init__(self):
        """Start with no metadata retained from earlier inputs."""
        self._entries = {}

    @staticmethod
    def _signature(path):
        """Read file identity and change indicators with stat."""
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns)

    def read(self, path):
        """Read metadata and reject file changes during probing.

        Probe errors are not cached. Stat before every reuse, including
        final validation, so replaced or modified candidates are probed
        again even when their path or size has stayed the same.
        """
        before = self._signature(path)
        cached = self._entries.get(path)
        if cached is not None and cached[0] == before:
            return cached[1]
        output = probe(path)
        if self._signature(path) != before:
            raise RuntimeError('媒体信息探测期间文件发生变化，不发布该输出。')
        self._entries[path] = (before, output)
        return output

    def replace(self, source, destination):
        """Promote a candidate with its metadata.

        Use only for this task's private workspace files. A rename may
        change ctime without changing media bytes; keep metadata only if
        its identity, size, and mtime still match the pre-move entry.
        Later reads check the destination's complete signature again.
        """
        before = self._signature(source)
        cached = self._entries.pop(source, None)
        self._entries.pop(destination, None)
        os.replace(source, destination)
        after = self._signature(destination)
        if (cached is not None and cached[0] == before
                and before[:4] == after[:4]):
            self._entries[destination] = (after, cached[1])


def verify_metadata(info):
    """Reject descriptive container and stream tags before publication.

    Permit structural muxer tags and HandBrake's new export timestamp.
    Stream languages must be three-letter codes; handler names must be
    generic. Rotation matrices and color data are separate from these
    tag dictionaries and are not removed or rewritten here.
    """
    if set(info.get('format_tags', {})) - FORMAT_TAGS:
        app_logging.event('validation_failed', category='probe', level='error',
                          reason='metadata_tags')
        raise RuntimeError('输出含未允许的描述性元数据，不发布该输出。')
    for index, stream in enumerate((info['video'], *info['audio'])):
        tags = stream.get('tags', {})
        handlers = ('', 'VideoHandler', 'SoundHandler')
        if (set(tags) - STREAM_TAGS
                or tags.get('handler_name', '') not in handlers
                or not re.fullmatch(r'[a-z]{3}', tags.get('language', 'und'))):
            app_logging.event('validation_failed', category='probe', level='error',
                              reason='metadata_tags', track_index=index,
                              has_track_name='name' in tags,
                              handler_generic=tags.get('handler_name', '') in handlers,
                              private_detail=json.dumps(tags, ensure_ascii=False))
            raise RuntimeError('输出含未允许的轨道元数据，不发布该输出。')


def verify(source, path, limit, *, probes=None):
    """Validate output and return its probe metadata.

    Args:
        source: Metadata returned by probe() for the original input.
        path: Completed local output to inspect before saving.
        limit: Exclusive upper size bound in decimal bytes.
        probes: Optional per-input cache for private output files.

    Check size, codec, geometry, duration, frames, tracks, and tags.
    Cached metadata requires a matching current file signature. Raise
    RuntimeError for invalid output; probe/parse errors propagate.
    """
    app_logging.event('validation_started', category='probe', level='info')
    output = probe(path) if probes is None else probes.read(path)
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
    verify_metadata(output)
    app_logging.event('validation_completed', category='probe', level='info',
                      output_bytes=path.stat().st_size)
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
    started = time.monotonic()
    app_logging.event('publish_started', category='publish',
                      output_bytes=temp.stat().st_size)
    number = 1
    while True:
        suffix = '_compressed' if number == 1 else f'_compressed_{number}'
        output = source.with_name(source.stem + suffix + '.mp4')
        try:
            destination = output.open('xb')
        except FileExistsError:
            number += 1
            continue
        except OSError as error:
            app_logging.event('publish_failed', category='publish', level='error',
                              reason='io_error', private_detail=str(error))
            raise
        try:
            with destination, temp.open('rb') as original:
                shutil.copyfileobj(original, destination, length=1024 * 1024)
            if output.stat().st_size != temp.stat().st_size:
                raise OSError('保存后的文件大小不一致。')
            with temp.open('rb') as original, output.open('rb') as saved:
                if hashlib.file_digest(original, 'sha256').digest() != hashlib.file_digest(saved, 'sha256').digest():
                    raise OSError('保存后的文件校验不一致。')
            app_logging.event('publish_completed', category='publish',
                              output_bytes=output.stat().st_size,
                              duration_ms=round((time.monotonic() - started) * 1000))
            return output
        except BaseException as error:
            app_logging.event('publish_failed', category='publish', level='error',
                              reason='io_error', private_detail=str(error))
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
            app_logging.event('recovery_retained', category='publish',
                              private_detail=str(working / 'best.mp4'))
            print(f'已保留编码结果，可直接复制到源目录，无需重新编码：{working / "best.mp4"}',
                  file=sys.stderr, flush=True)
        else:
            shutil.rmtree(working, ignore_errors=True)
        raise
    else:
        shutil.rmtree(working, ignore_errors=True)


def encode(command, log):
    """Run HandBrake with concurrent pipe draining and terminal progress."""
    def progress(line):
        if 'Encoding:' in line:
            print('\r' + line.strip()[:160].ljust(100), end='', flush=True)

    code = app_logging.run_process(command, log, tool='HandBrakeCLI',
                                   progress=progress)
    print()
    if code:
        raise RuntimeError(f'HandBrake 编码失败（{code}）。诊断任务：{log}')


def remux(source, info, output, log):
    """Copy the primary video and all audio tracks into a workspace MP4.

    Drop descriptive global/stream tags, retaining only valid language
    codes. Stream-copy keeps display matrices and codec color data.
    Retry with AAC audio on failure, keeping video stream-copy enabled.
    Raise RuntimeError if both attempts fail. The caller checks output.
    """
    # The candidate lives in scratch space; -y can replace it safely.
    command = [tool('ffmpeg'), '-hide_banner', '-nostdin', '-loglevel', 'warning',
               '-y', '-copyts', '-start_at_zero', '-i', str(source),
               '-map', f'0:{info["video"]["index"]}', '-map', '0:a?',
               '-map_metadata', '-1', '-map_metadata:s', '-1',
               '-map_chapters', '-1', '-c', 'copy', '-movflags', '+faststart']
    # Copy language codes, excluding names, locations, and dates.
    for kind, streams in (('v', [info['video']]), ('a', info['audio'])):
        for index, stream in enumerate(streams):
            language = stream.get('tags', {}).get('language', 'und')
            if re.fullmatch(r'[a-z]{3}', language):
                command += [f'-metadata:s:{kind}:{index}', f'language={language}']
    command.append(str(output))
    for audio_retry in (False, True):
        if audio_retry:
            print('直接封装未成功，重试仅将音频转换为 AAC；视频继续直接复制。', flush=True)
            command = command[:-1] + ['-c:a', 'aac', '-b:a', '128k', command[-1]]
        code = app_logging.run_process(command, log, tool='ffmpeg')
        if code == 0:
            return
        if not info['audio']:
            break
    raise RuntimeError(f'FFmpeg 重新封装失败，未发布输出。任务编号：{log}')


def transcode(source, info, limit, working, log, *, probes=None):
    """Return the best valid candidate after up to four tries.

    Estimate video bitrate from duration and the audio budget.
    Correct the budget using measured sizes and each attempt's metadata.
    Share a per-input ProbeCache with final validation when supplied.
    Keep valid candidates in working/best.mp4; do not publish here.
    Raise RuntimeError if no attempt produces an acceptable output.
    """
    probes = ProbeCache() if probes is None else probes
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
        app_logging.event('encode_attempt', category='encode', attempt=attempt,
                          video_kbps=video_kbps, audio_kbps=audio_kbps)
        print(f'第 {attempt} 次编码：视频 {video_kbps} kbps，音频每轨 {audio_kbps} kbps', flush=True)
        # Disable layout names along with source-name passthrough.
        command = [tool('HandBrakeCLI'), '--preset-import-file', str(ROOT / 'preset.json'),
                   '-Z', 'ClipFitTT', '-i', str(source), '-o', str(candidate),
                   '--min-duration', '0', '-f', 'av_mp4', '-e', 'x264',
                   '--encoder-preset', 'medium', '-b', str(video_kbps),
                   '--multi-pass', '--turbo', '--vfr', '--crop-mode', 'none',
                   '--crop', '0:0:0:0', '--no-comb-detect', '--no-decomb',
                   '--no-deinterlace', '--no-detelecine', '--no-markers',
                   '--optimize', '--subtitle', 'none',
                   '--no-metadata', '--no-keep-aname', '--no-keep-subname',
                   '--automatic-naming-behaviour', 'off']
        if count:
            command += ['--all-audio', '-E', 'ca_aac', '-B', str(audio_kbps),
                        '--mixdown', 'stereo']
        else:
            command += ['-a', 'none']
        encode(command, log)
        actual = candidate.stat().st_size
        app_logging.event('encode_candidate', category='encode', attempt=attempt,
                          output_bytes=actual)
        print(f'实际大小：{actual / 1e6:.3f} MB', flush=True)
        if actual < limit:
            output_info = verify(info, candidate, limit, probes=probes)
            # A later attempt can be smaller; retain the largest valid one.
            if not best.exists() or actual > best.stat().st_size:
                probes.replace(candidate, best)
            if actual >= limit * 0.99:
                break
            if previous and previous[1] < limit and actual <= previous[1] * 1.002:
                # Stop retrying when another valid attempt adds little size.
                print('编码器已接近该内容的有效码率上限，保留较小输出。')
                break
        else:
            # Oversize output still needs audio metadata for the next
            # bitrate estimate, but cannot pass the size validation.
            output_info = probes.read(candidate)
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
        raise RuntimeError(f'多次编码后仍超过大小上限，未生成最终文件。任务编号：{log}')
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
    probes = ProbeCache()
    size = source.stat().st_size
    codec = info['video']['codec_name']
    app_logging.event('input_ready', category='encode', input_bytes=size,
                      audio_tracks=len(info['audio']))
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
                verify(info, packed, limit, probes=probes)
                best = working / 'best.mp4'
                probes.replace(packed, best)
            else:
                print('重新封装达到或超过大小上限，自动转入 HandBrake 转码。', flush=True)
                app_logging.event('remux_oversize', category='encode',
                                  output_bytes=actual, reason='size')
                packed.unlink()
        if best is None:
            print('处理方式：HandBrake 限大小转码。', flush=True)
            best = transcode(source, info, limit, working, log, probes=probes)
        verify(info, best, limit, probes=probes)
        print('正在保存到源目录并校验文件……', flush=True)
        result = publish(best, source)
        print('保存校验通过：文件大小和 SHA-256 一致。', flush=True)
        print(f'完成：{result}\n大小：{result.stat().st_size / 1e6:.3f} MB\n任务编号：{log}', flush=True)
        return result


def compress(source, limit):
    """Clean expired files and process an input in a correlated log task.

    Record failures, including cancellation, before propagating them to the
    batch handler. Workspace cleanup/recovery is managed independently.
    """
    removed = clean_logs()
    with task_log() as log:
        app_logging.event('logs_pruned', category='app', level='info', removed=removed)
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
    """Run serialized videos, then check updates outside the batch lock.

    Return a nonzero status if any file fails. Release the batch lock
    before checking updates or waiting for consent. The updater locks
    version probes and approved installation, so an unanswered prompt
    cannot block this tool's queued video jobs.
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
    with app_logging.batch_scope(ROOT) as batch:
        batch.event('batch_started', files=len(dict.fromkeys(args.files)))
        return run_batch(args, batch)


def run_batch(args, batch):
    """Serialize media work and correlate its separate update task."""
    failures = 0
    with (ROOT / '.queue.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            batch.event('queue_wait', reason='queue_busy')
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
            app_logging.event('update_failed', category='update', level='error',
                              status='failed', private_detail=str(error))
            print(f'HandBrakeCLI 更新检查未完成：{error}。已完成的视频不受影响。', file=sys.stderr)
    batch.event('batch_completed', failures=failures)
    print(f'\n全部处理结束，失败 {failures} 个。', flush=True)
    return int(bool(failures))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n已取消，源文件保持不变。', file=sys.stderr)
        sys.exit(130)
