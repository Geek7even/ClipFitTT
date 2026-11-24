#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile a temporary bridge and verify real macOS log privacy."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app_logging


def main():
    """Observe synthetic native events without changing logging settings.

    Require macOS, a compiler, and permission to stream logs. Use only
    random test identifiers and a synthetic private sentinel. Do not
    print raw system log records, which contain system image paths.
    """
    if sys.platform != 'darwin':
        raise SystemExit('This optional native smoke test requires macOS.')
    with tempfile.TemporaryDirectory(prefix='clipfittt-oslog-smoke-') as folder:
        root = Path(folder)
        if not app_logging.build_native(root, ROOT / 'oslog_bridge.c'):
            raise SystemExit('Native smoke test requires an available macOS compiler.')
        backend = app_logging.NativeBackend(root)
        batch, task = uuid.uuid4().hex[:16], uuid.uuid4().hex[:16]
        sentinel = 'SYNTHETIC_PRIVATE_' + uuid.uuid4().hex
        predicate = f'subsystem == "{app_logging.SUBSYSTEM}" AND eventMessage CONTAINS "{batch}"'
        command = ['/usr/bin/log', 'stream', '--style', 'ndjson', '--level', 'info',
                   '--predicate', predicate]
        observed = False
        with subprocess.Popen(command, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, bufsize=0) as stream:
            try:
                pending = b''
                deadline, emit_at = time.monotonic() + 12, 0
                with selectors.DefaultSelector() as selector:
                    selector.register(stream.stdout, selectors.EVENT_READ, 'stdout')
                    selector.register(stream.stderr, selectors.EVENT_READ, 'stderr')
                    while time.monotonic() < deadline and not observed:
                        now = time.monotonic()
                        if now >= emit_at:
                            backend.write('notice', 'app', 'native_logging_smoke',
                                          batch, task, '{"output_bytes":1}', sentinel)
                            emit_at = now + 0.3
                        for key, _ in selector.select(timeout=0.2):
                            chunk = os.read(key.fd, 8192)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            if key.data != 'stdout':
                                continue
                            pending += chunk
                            lines = pending.split(b'\n')
                            pending = lines.pop()[-65536:]
                            for line in lines:
                                try:
                                    message = json.loads(line).get('eventMessage', '')
                                except (ValueError, AttributeError):
                                    continue
                                if task in message and 'native_logging_smoke' in message:
                                    assert sentinel not in message, (
                                        'Private data was visible in capture')
                                    assert '<private>' in message
                                    observed = True
                        if stream.poll() is not None:
                            break
            finally:
                stream.terminate()
                try:
                    stream.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    stream.kill()
                    stream.wait()
        assert observed, 'No native event observed; check log-stream permissions/configuration'
        print('PASS: native bridge ABI, subsystem/category event delivery,')
        print('public task correlation and private-field redaction; '
              'no logging configuration changed.')


if __name__ == '__main__':
    main()
