#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Check candidate Git files for runtime artifacts and common private data."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_SUFFIXES = {'.mp4', '.mov', '.m4v', '.dmg', '.zip', '.log', '.pyc',
                    '.dylib', '.so', '.o'}
BLOCKED_PARTS = {'vendor', 'bin', 'logs', 'work', 'third_party', '__pycache__', '.build', '.aws', '.ssh', '.codex', '.agents'}
PATTERNS = {
    'absolute user or mounted-volume path': re.compile(r'/(?:Users|Volumes)/[A-Za-z0-9_\u4e00-\u9fff][^\s"\'<>]*'),
    'private IPv4 address': re.compile(r'\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'),
    'credential-like value': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b'),
    'private key': re.compile(r'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----'),
}


def candidates():
    """Return unique Git candidate paths after requiring index consistency.

    A clean working-tree/index comparison prevents scanning sanitized files
    while different, possibly private content remains staged for a commit.
    Include tracked files and nonignored untracked files, in stable order.
    """
    state = subprocess.run(['git', 'diff', '--quiet'], cwd=ROOT)
    if state.returncode:
        raise RuntimeError('Working tree differs from staged files; review and restage changes before release checks.')
    result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                            cwd=ROOT, capture_output=True, check=True)
    return sorted(set(Path(name) for name in result.stdout.decode().split('\0') if name))


def check(paths):
    """Return review findings for candidate paths relative to the repository.

    Reject runtime artifacts, links, binaries, unexpected large files, and
    common private-data patterns. This is a release guard, not an exhaustive
    credential scanner; a human still reviews contents and Git identity.
    """
    findings = []
    for relative in paths:
        path = ROOT / relative
        if (set(relative.parts) & BLOCKED_PARTS or relative.suffix.lower() in BLOCKED_SUFFIXES
                or any(part.endswith('.workflow') for part in relative.parts)
                or relative.name in {'.DS_Store', '.queue.lock', 'runtime_config.json'}
                or relative.name.startswith('.env')):
            findings.append(f'{relative}: runtime/private artifact must not be published')
            continue
        if path.is_symlink():
            findings.append(f'{relative}: review/remove symlink before publishing')
            continue
        if not path.is_file():
            continue
        if path.stat().st_size > 1_000_000:
            findings.append(f'{relative}: unexpected large source file')
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeError:
            findings.append(f'{relative}: unexpected binary file')
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                findings.append(f'{relative}: possible {label}; inspect before publishing')
    return findings


def main():
    """Print release findings and return 0 only when all checks pass."""
    try:
        paths = candidates()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1
    findings = check(paths)
    if findings:
        print('\n'.join(findings), file=sys.stderr)
        return 1
    print(f'Release check passed for {len(paths)} candidate source files. Review Git identity and staged diff separately.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
