# ClipFitTT privacy

## Local video processing

Videos are processed locally with external HandBrakeCLI/FFmpeg executables.
This tool does not upload video contents, source paths, logs, or hashes.
Source folders can be on an SMB share; reading/writing that share uses the
computer's existing operating-system connection to that server.

## Network access

At the end of each batch, the tool checks the public HandBrake GitHub release
API for a newer stable CLI. GitHub receives normal HTTPS request information
such as IP address and the tool's User-Agent. No source filename, media data,
local path, or log is added to these requests. Official package downloads also
use GitHub's release delivery hosts. Downloads/updates require explicit consent
or the installer's `--download-cli` option. Update checks never update the GUI.
An unavailable network is reported without invalidating completed videos.

## Local files that may contain private information

- Generated `.workflow` and `runtime_config.json`: absolute interpreter,
  dependency, and installation paths, potentially including a local username.
- Failure diagnostics: a bounded tail of source/output paths, filenames,
  codec data, commands, and errors. Filenames omit source names, but diagnostic
  **contents are not anonymized**. Each file is capped at 324 KiB and created
  with mode 0600. Earlier output may be omitted, so it is not a full transcript.
- Successful fallback logs: controlled events only; raw terminal/tool output
  and private detail fields are not written to these files.
- Recovery data in `work/`: complete processed video if saving fails.

These files are generated on the user's computer and are excluded by
`.gitignore`. Logs older than seven days by modification time are removed
before the next file is processed; this is not a background timer. Recovery
videos remain until manually recovered or deleted. Source/output media
may contain private content even after the sharing metadata cleanup below.

## macOS Unified Logging

The optional local bridge uses the subsystem
`io.github.geek7even.clipfittt`. Public fields contain controlled event names,
version, random batch/task identifiers, counts, sizes, durations, and finite
status/reason values. Paths, exception details, and actual metadata are sent
only through explicit private fields, hidden by default. Tool transcripts
are not streamed into Unified Logging. Public technical fields may still
reveal activity patterns; private-field formatting is not anonymization.

The operating system manages retention and access. Seven-day file cleanup
does not delete unified events, and system retention is not guaranteed to
last seven days. Info/debug events need not be persisted. Administrators
may configure different privacy/access policies; exported records can also
include system image/library paths. Review exports before sharing them.
ClipFitTT does not enable private-data collection or change system logging
configuration. It compiles its bridge only when build tools are already
available and never installs those tools. Missing or unusable native logging
falls back to bounded local files without failing the media operation.

## Sharing metadata

Both remuxing and HandBrake transcoding disable source descriptive metadata
and custom track-name passthrough. Remuxing whitelists three-letter stream
language codes instead of copying stream tag dictionaries. Source capture
locations/dates, device descriptions, author/title/comment fields, and custom
track names are not intentionally transferred to the output. Output container
and stream tags are checked against a whitelist before publication; unexpected
descriptive fields or custom handler names cause the output to be rejected.

Structural MP4 tags, generic encoder/handler information, language codes, and
HandBrake's newly generated export timestamps may remain. Rotation/display
matrices and codec color information are playback data, not descriptive tags;
the metadata cleanup does not strip them. The existing lossy encoding and
frame/geometry validation policies still apply. Source files are not modified.

This is metadata minimization, not complete anonymization. It does not redact
faces, voices, text within the video, filenames, or user data embedded inside
copied codec bitstreams. It also does not anonymize local logs or recovery
files, which can still contain private paths or complete media.

Review and redact diagnostics, media metadata, and screenshots before posting
an issue. Never attach customer media or a complete unreviewed process log.

## Publishing a fork

Use `python3 tools/check_release.py` and review staged files. Git author names
and email addresses are stored in commits; use your GitHub-provided noreply
address if you do not want to disclose a personal email. The checker does not
guarantee that all sensitive material is detected. Review the actual files and
commit identity before pushing.
