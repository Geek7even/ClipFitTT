# SPDX-License-Identifier: GPL-2.0-only
"""Emit safe unified events and retain bounded local failure diagnostics."""
import codecs
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
import ctypes
from datetime import datetime
import json
import math
import os
from pathlib import Path
import platform
import re
import selectors
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from zoneinfo import ZoneInfo

SUBSYSTEM = 'io.github.geek7even.clipfittt'
BRIDGE_NAME = 'libclipfittt_log.dylib'
DIAGNOSTIC_LIMIT = 256 * 1024
EVENT_LIMIT = 64 * 1024
MAX_FILE_BYTES = DIAGNOSTIC_LIMIT + EVENT_LIMIT + 4096
LOG_PATTERN = re.compile(
    r'^(?:encode-[A-Za-z0-9_-]+|\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}'
    r'__(?:process|update|diagnostic|fallback)__[A-Za-z0-9_-]+)\.log$')
LEVELS = {'notice': 0, 'info': 1, 'debug': 2, 'error': 3, 'fault': 4}
CATEGORIES = {'app', 'probe', 'encode', 'publish', 'update', 'install'}
NUMERIC_FIELDS = {
    'input_bytes', 'output_bytes', 'duration_ms', 'attempt', 'video_kbps',
    'audio_kbps', 'audio_tracks', 'width', 'height', 'returncode', 'files',
    'failures', 'removed', 'track_index', 'has_track_name', 'handler_generic',
}
STRING_FIELDS = {
    'tool': {'HandBrakeCLI', 'ffmpeg', 'ffprobe'},
    'stream': {'video', 'audio'},
    'reason': {'timeout', 'nonzero_exit', 'cancelled', 'exception', 'queue_busy',
               'metadata_tags', 'handler_name', 'language', 'size', 'codec',
               'dimensions', 'duration', 'frames', 'audio_tracks', 'io_error',
               'declined', 'noninteractive', 'already_current', 'unavailable'},
    'status': {'current', 'deferred', 'declined', 'updated', 'cancelled',
               'failed', 'fallback', 'native'},
}
_TASK = ContextVar('clipfittt_task', default=None)
_BATCH = ContextVar('clipfittt_batch', default=None)


def _print(message):
    """Report logging status without letting a closed console fail a job."""
    try:
        print(message, file=sys.stderr, flush=True)
    except Exception:
        pass


class TailBuffer:
    """Keep at most limit bytes, discarding the oldest diagnostics first."""

    def __init__(self, limit):
        """Initialize an empty UTF-8 byte tail with a fixed memory bound."""
        self.limit = limit
        self.data = bytearray()
        self.dropped = 0

    def write(self, text):
        """Append text without retaining an unbounded tool transcript."""
        encoded = text.encode('utf-8', errors='replace')
        excess = max(0, len(self.data) + len(encoded) - self.limit)
        self.dropped += excess
        self.data.extend(encoded[-self.limit:])
        if len(self.data) > self.limit:
            del self.data[:-self.limit]

    def text(self):
        """Decode the retained tail, marking any discarded earlier bytes."""
        prefix = f'[earlier {self.dropped} bytes omitted]\n' if self.dropped else ''
        return prefix + self.data.decode('utf-8', errors='replace')


class NativeBackend:
    """Call a locally built bridge through a fixed, checked C ABI."""

    def __init__(self, root):
        """Load only this installation's optional native logging library."""
        self.library = ctypes.CDLL(str(Path(root) / 'bin' / BRIDGE_NAME))
        abi = self.library.clipfittt_log_abi_version
        abi.argtypes, abi.restype = [], ctypes.c_int
        if abi() != 1:
            raise ValueError('Unsupported logging bridge ABI')
        self.emit = self.library.clipfittt_log_emit
        self.emit.argtypes = [ctypes.c_int, *([ctypes.c_char_p] * 6)]
        self.emit.restype = ctypes.c_int

    def write(self, level, category, name, batch, task, fields, detail):
        """Emit public whitelisted fields and a separately private detail."""
        strings = [category, name, batch, task, fields, detail[:1024]]
        args = [value.encode('utf-8', errors='replace') for value in strings]
        if self.emit(LEVELS[level], *args):
            raise RuntimeError('Logging bridge rejected the event')


def build_native(root, source):
    """Optionally compile a private bridge without installing build tools.

    Return False when no macOS compiler is available or compilation fails.
    No compiler/toolchain download or system logging configuration occurs.
    The caller installs the generated binary using its usual rollback path.
    """
    if sys.platform != 'darwin':
        return False
    output = Path(root) / 'bin' / BRIDGE_NAME
    try:
        compiler = subprocess.run(['/usr/bin/xcrun', '--find', 'clang'],
                                  capture_output=True, text=True,
                                  check=True, timeout=10).stdout.strip()
        sdk = subprocess.run(['/usr/bin/xcrun', '--sdk', 'macosx', '--show-sdk-path'],
                             capture_output=True, text=True,
                             check=True, timeout=10).stdout.strip()
        architecture = platform.machine()
        if architecture not in ('arm64', 'x86_64'):
            return False
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.oslog-build-', dir=root) as folder:
            binary = Path(folder) / BRIDGE_NAME
            subprocess.run([compiler, '-dynamiclib', '-std=c11', '-O2',
                            '-arch', architecture, '-isysroot', sdk,
                            str(source), '-o', str(binary)],
                           capture_output=True, check=True, timeout=60)
            os.replace(binary, output)
        NativeBackend(root)
        return True
    except Exception:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def clean_logs(root, now=None):
    """Prune owned files older than seven days; ignore links and other data."""
    try:
        folder = Path(root) / 'logs'
        folder.mkdir(exist_ok=True)
        cutoff = (time.time() if now is None else now) - 7 * 24 * 60 * 60
        removed = 0
        for path in folder.iterdir():
            if not LOG_PATTERN.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                _print('部分旧诊断日志未能清理，视频处理继续。')
        return removed
    except OSError:
        _print('日志目录不可用，视频处理继续。')
        return 0


class TaskLog:
    """Correlate public events and buffer private diagnostics for one task."""

    def __init__(self, root, kind, batch_id=None, version=None):
        """Start a task without requiring a writable log directory."""
        self.root, self.kind = Path(root), kind
        self.batch_id = batch_id or uuid.uuid4().hex[:16]
        self.task_id = uuid.uuid4().hex[:16]
        self.events = TailBuffer(EVENT_LIMIT)
        self.diagnostics = TailBuffer(DIAGNOSTIC_LIMIT)
        self.failed = False
        self.path = None
        self.started = time.monotonic()
        try:
            self.backend = NativeBackend(self.root)
        except Exception:
            self.backend = None
        self.fallback = self.backend is None
        try:
            self.version = version or (self.root / 'VERSION').read_text().strip()
        except Exception:
            self.version = 'unknown'
        if (not isinstance(self.version, str) or len(self.version) > 64
                or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?', self.version)):
            self.version = 'unknown'

    def __str__(self):
        """Expose an opaque task identifier instead of a nonexistent file."""
        return self.task_id

    def capture(self, text):
        """Buffer private text without allowing diagnostics to fail media."""
        try:
            self.diagnostics.write(text)
        except Exception:
            pass

    def event(self, name, *, category=None, level='notice', private_detail='', **fields):
        """Emit controlled fields; arbitrary strings never become public.

        Private detail is only sent to the native private argument. It is
        deliberately absent from fallback events. Raw tool output is kept
        separately and written only for failed tasks.
        """
        try:
            if level not in LEVELS or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name):
                return
            category = category or {'process': 'encode'}.get(self.kind, self.kind)
            category = category if category in CATEGORIES else 'app'
            data = {'version': self.version}
            for key, value in fields.items():
                if (key in NUMERIC_FIELDS and isinstance(value, (int, float))
                        and math.isfinite(value)):
                    data[key] = value
                elif (key in STRING_FIELDS and isinstance(value, str)
                      and value in STRING_FIELDS[key]):
                    data[key] = value
            payload = json.dumps(data, ensure_ascii=True, separators=(',', ':'))
            record = dict(event=name, category=category, level=level,
                          batch_id=self.batch_id, task_id=self.task_id, data=data)
            self.events.write(json.dumps(record, ensure_ascii=True) + '\n')
            if level in ('error', 'fault'):
                self.failed = True
                if private_detail:
                    self.capture(f'PRIVATE EVENT {name}: {private_detail}\n')
            if self.backend is not None:
                try:
                    self.backend.write(level, category, name, self.batch_id,
                                       self.task_id, payload, str(private_detail))
                except Exception:
                    self.backend = None
                    self.fallback = True
        except Exception:
            self.fallback = True

    def finish(self):
        """Keep bounded failure diagnostics or sanitized fallback events.

        Success with a working native backend creates no log file. Saving
        is best effort and never changes the media task's result.
        """
        try:
            self.event('task_failed' if self.failed else 'task_completed',
                       level='error' if self.failed else 'notice',
                       duration_ms=round((time.monotonic() - self.started) * 1000))
            if not self.failed and not self.fallback:
                return
            folder = self.root / 'logs'
            folder.mkdir(exist_ok=True)
            stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d_%H-%M-%S')
            kind = 'diagnostic' if self.failed else 'fallback'
            fd, filename = tempfile.mkstemp(
                prefix=f'{stamp}__{kind}__{self.kind}__{self.task_id}__',
                suffix='.log', dir=folder)
            self.path = Path(filename)
            with os.fdopen(fd, 'wb') as handle:
                header = f'ClipFitTT {kind}; task={self.task_id}; batch={self.batch_id}\n'
                text = header + self.events.text()
                if self.failed:
                    text += ('\nPRIVATE LOCAL DIAGNOSTICS (bounded tail)\n'
                             + self.diagnostics.text())
                handle.write(text.encode('utf-8', errors='replace')[-MAX_FILE_BYTES:])
            _print(f'诊断文件：{self.path}' if self.failed else f'文件日志回退：{self.path}')
        except Exception:
            _print(f'诊断日志无法保存；任务编号：{self.task_id}')


class ConsoleCapture:
    """Display terminal output while buffering only useful diagnostic text."""

    def __init__(self, console, task):
        """Retain streams owned by the surrounding task context."""
        self.console, self.task = console, task

    def write(self, text):
        """Keep progress on screen without flooding diagnostic buffers."""
        try:
            self.console.write(text)
        except Exception:
            pass
        if not text.startswith('\rEncoding:'):
            self.task.capture(text)
        return len(text)

    def flush(self):
        """Flush the terminal, tolerating closure during a background job."""
        try:
            self.console.flush()
        except Exception:
            pass

    def isatty(self):
        """Preserve terminal interactivity independently of log storage."""
        try:
            return self.console.isatty()
        except Exception:
            return False


def event(name, **fields):
    """Emit within the current task; direct library use stays side-effect free."""
    task = _TASK.get()
    if task is not None:
        task.event(name, **fields)


@contextmanager
def batch_scope(root):
    """Give file processing and the later update check one batch identifier."""
    batch = TaskLog(root, 'app')
    token = _BATCH.set((Path(root), batch.batch_id))
    try:
        yield batch
    except BaseException as error:
        batch.failed = True
        batch.capture(''.join(traceback.format_exception(error)))
        batch.event('batch_error', level='error', reason='exception',
                    private_detail=str(error))
        raise
    finally:
        _BATCH.reset(token)
        batch.finish()


@contextmanager
def task_scope(root, kind='process', *, version=None):
    """Log one task without persisting raw output from successful runs."""
    batch = _BATCH.get()
    batch_id = batch[1] if batch and batch[0] == Path(root) else None
    task = TaskLog(root, kind, batch_id, version)
    token = _TASK.set(task)
    try:
        with (redirect_stdout(ConsoleCapture(sys.stdout, task)),
              redirect_stderr(ConsoleCapture(sys.stderr, task))):
            print(f'任务编号：{task.task_id}；批次编号：{task.batch_id}', flush=True)
            task.event('task_started', status='fallback' if task.fallback else 'native')
            try:
                yield task
            except BaseException as error:
                task.failed = True
                task.capture(''.join(traceback.format_exception(error)))
                cancelled = isinstance(error, KeyboardInterrupt)
                task.event('task_cancelled' if cancelled else 'task_error',
                           level='notice' if cancelled else 'error',
                           reason='cancelled' if cancelled else 'exception',
                           private_detail=str(error))
                raise
    finally:
        _TASK.reset(token)
        task.finish()


def run_process(command, task, *, tool, progress=None):
    """Drain both tool pipes with bounded reads, retaining only a private tail.

    Read readiness avoids pipe deadlocks and unbounded readline buffers.
    Terminate/reap the child on cancellation. A failed sub-attempt does
    not itself mark the whole task failed, allowing the AAC retry path.
    """
    task.capture('\nCOMMAND ' + shlex.join(map(str, command)) + '\n')
    event('tool_started', category='encode', level='info', tool=tool)
    started = time.monotonic()
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, bufsize=0) as process:
        pending = ''
        decoders = {key: codecs.getincrementaldecoder('utf-8')(errors='replace')
                    for key in ('stdout', 'stderr')}
        exited = None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
                selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
                while selector.get_map():
                    for key, _ in selector.select(timeout=0.2):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            tail = decoders[key.data].decode(b'', final=True)
                            task.capture(tail)
                            selector.unregister(key.fileobj)
                            continue
                        text = decoders[key.data].decode(chunk)
                        if key.data == 'stderr' or progress is None:
                            task.capture(text)
                        else:
                            pending += text.replace('\r', '\n')
                            lines = pending.split('\n')
                            pending = lines.pop()[-8192:]
                            for line in lines:
                                if line and 'Encoding:' not in line:
                                    task.capture(line + '\n')
                                try:
                                    progress(line[-8192:])
                                except Exception:
                                    pass
                    if process.poll() is not None:
                        exited = exited or time.monotonic()
                        # An unrelated descendant must not hold us open forever.
                        if time.monotonic() - exited > 2:
                            break
            code = process.wait()
            if pending and 'Encoding:' not in pending:
                task.capture(pending)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    event('tool_exited', category='encode', level='info', tool=tool,
          returncode=code, duration_ms=round((time.monotonic() - started) * 1000))
    return code
