#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a source-only archive from reviewed Git candidate files."""
import hashlib
from pathlib import Path
import zipfile

import check_release

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Write a checked source ZIP and checksum beside the repository.

    Use the version in VERSION for both filenames and the archive root.
    Prepare a new version before running this for another published release:
    an existing output with the same version would be replaced.
    """
    paths = check_release.candidates()
    findings = check_release.check(paths)
    if findings:
        raise SystemExit('\n'.join(findings))
    version = (ROOT / 'VERSION').read_text().strip()
    prefix = f'clipfittt-{version}'
    archive = ROOT.parent / f'{prefix}-source.zip'
    # A fixed timestamp avoids embedding local file modification times.
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as handle:
        for relative in paths:
            data = (ROOT / relative).read_bytes()
            info = zipfile.ZipInfo(f'{prefix}/{relative.as_posix()}', date_time=(2026, 11, 24, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            # Store a regular source-file mode without local ownership data.
            info.external_attr = 0o100644 << 16
            handle.writestr(info, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sums = ROOT.parent / f'{prefix}-SHA256SUMS.txt'
    sums.write_text(f'{digest}  {archive.name}\n')
    print(f'Source archive: {archive.name}\nSHA-256: {digest}\nFiles: {len(paths)}')


if __name__ == '__main__':
    main()
