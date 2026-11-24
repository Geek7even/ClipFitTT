# Changelog

## 0.9.4-beta.1 — 2026-11-24

- Package the hybrid logging improvements as ClipFitTT Beta 0.9.4
  (`0.9.4-beta.1`), the first prerelease in the 0.9.4 series.

- Send controlled lifecycle events to macOS Unified Logging through an
  optional locally compiled bridge, correlating batch and task identifiers.
  Keep paths and raw metadata in explicit private fields, and fall back to
  sanitized event files when the native backend is unavailable.
- Preserve terminal progress and save bounded private diagnostic tails only
  for failed tasks. Limit each diagnostic file to 324 KiB with mode 0600;
  retain seven-day cleanup for owned legacy, diagnostic, and fallback files.
- Drain HandBrake/FFmpeg stdout and stderr concurrently without unbounded
  transcripts; terminate and reap the child on cancellation. Keep logging
  failures independent of media results and installer rollback.
- Add offline failure/privacy tests and optional synthetic native-log and
  isolated installation/media verification. Exclude native build artifacts
  from source distributions.

## 0.9.3-beta.2 — 2026-11-24

- Disable HandBrake's automatic audio naming in both preset and CLI options.
  Unnamed stereo tracks can otherwise acquire generated name/handler tags
  that are rejected by the strict sharing metadata policy.
- Add synthetic regression coverage for unknown-language unnamed stereo,
  with and without timecode, without relaxing metadata validation.

## 0.9.3-beta.1 — 2026-11-24

- Package the four queue, probing, metadata-reuse, and sharing-privacy
  improvements as ClipFitTT Beta 0.9.3 (`0.9.3-beta.1`).

- Disable descriptive metadata and custom track-name passthrough on both
  output paths; preserve valid language codes and playback metadata, and
  reject unexpected output tags before publication. Document remaining export
  timestamps, codec data, and local diagnostic privacy boundaries.
- Exercise tagged synthetic MOVs through remuxing, HandBrake conversion, and
  the audio-only AAC retry, checking rotation, SDR color, and source retention.
- Reuse metadata within each input's private workspace, invalidate entries
  when candidates change, and retain full pre-publication validation without
  launching duplicate probes for unchanged remux or transcode results.
- Bound each ffprobe metadata subprocess to 60 seconds, report actionable
  timeout errors, continue the batch, and preserve completed local candidates
  when output validation times out. Encoding duration remains unrestricted.
- Release the batch queue lock before querying or prompting for HandBrakeCLI
  updates, allowing queued video jobs to run while consent is pending.
- Serialize CLI version probes and approved installation with the same queue
  lock, rechecking the version before installation to avoid duplicate updates
  or downgrades after another task has updated the CLI.

## 0.9.2-beta.1 — 2026-11-24

- Update the displayed version to ClipFitTT Beta 0.9.2, technical version
  0.9.2-beta.1, including installed Finder service metadata.
- Document mandatory privacy review before every GitHub push.
- Compression behavior and third-party dependency requirements are unchanged.

- Document callable contracts and key media, update, installation, and recovery
  decisions without changing executable logic.
- Standardized project credits: Designed by Geek7even; Developed entirely with AI.
- Documented the credits convention for descriptions, release notes and future About views.

## 0.9.1-beta.1 — 2026-11-13

- First beta package, displayed as ClipFitTT Beta 0.9.1.
- Project name: ClipFitTT; use case: preparing videos for TeamTalk mobile preview.
- English project/delivery paths, branded Finder service and CLI preset.
- README/project description explicitly disclose AI-only programming.
- Source-only macOS Finder compression workflow prepared for public beta testing.
- Same-source dimensions/frame rate, H.264 MP4 output below decimal 100 MB.
- Small H.264 inputs are remuxed; oversized remux results fall back to encoding.
- SMB-compatible, non-overwriting saves with size/SHA-256 validation and recovery.
- Timestamped seven-day logs and consent-based HandBrakeCLI updates.
- Portable Python/FFmpeg discovery, official CLI provisioning, and generated
  per-user workflows without publisher-specific filesystem paths.
- GPL-2.0-only license, third-party provenance, privacy guidance, and tests.
