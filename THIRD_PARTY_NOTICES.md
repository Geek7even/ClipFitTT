# Third-party notices

This is an independent Finder automation project. It is not an official
HandBrake or FFmpeg distribution and does not claim endorsement by either.
This repository and its source release archives do not contain third-party
executables, installers, libraries, or media.

## HandBrake / HandBrakeCLI

- Project and source: https://github.com/HandBrake/HandBrake
- Official downloads: https://handbrake.fr/downloads2.php
- Upstream license notice: https://github.com/HandBrake/HandBrake/blob/master/LICENSE
- GPLv2 text: https://github.com/HandBrake/HandBrake/blob/master/COPYING
- Component notices: https://github.com/HandBrake/HandBrake/blob/master/THANKS.markdown

HandBrake is copyrighted by the HandBrake contributors and other upstream
copyright holders. Compiled HandBrake builds are licensed under GPLv2;
individual source components have the licenses identified upstream.

`preset.json` was generated with the official HandBrakeCLI 1.11.2 preset
exporter and modified for this project's H.264/MP4, same-source frame rate,
no-crop, two-pass, all-audio workflow. Its schema and exported defaults come
from HandBrake. No independent authorship is claimed for upstream material;
any applicable upstream GPLv2 terms remain in effect. The project distribution
uses GPL-2.0-only, with the complete text in LICENSE.

The installer can use an existing, compatible CLI or download an unmodified
official macOS CLI after explicit authorization. Downloaded DMGs are verified
against SHA-256 metadata from the official GitHub release. Available upstream
license/author notices from the DMG are retained locally under
`third_party/HandBrake/`, together with the official release URL, version, and
digest. These local downloads are not part of this source repository.

If you redistribute HandBrake binaries yourself, review and comply with the
binary's upstream license and corresponding-source requirements. Merely
linking to this project's source does not fulfill those requirements.

## FFmpeg and ffprobe

- Project and source: https://ffmpeg.org/
- Licensing: https://ffmpeg.org/legal.html
- Source: https://git.ffmpeg.org/ffmpeg.git

FFmpeg's effective license depends on its build configuration, including
LGPL/GPL options and enabled components. This project invokes external
executables and does not bundle FFmpeg, ffprobe, or their libraries. Users
obtain their own builds; this project's license does not replace their terms.

## Python and macOS

Python is a separately installed interpreter under its own PSF license:
https://docs.python.org/3/license.html

Finder, Automator, Terminal, and system command-line utilities are platform
components supplied by Apple. This repository generates an Automator workflow
using plist data and references installed system actions; it does not include
Apple executable code, action bundles, logos, or application assets.
