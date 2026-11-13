# Contributing

By submitting a contribution, you agree to license it under GPL-2.0-only.
Retain upstream notices and identify any new third-party content and license.

1. Use Python 3.11+ and keep the project free of mandatory pip dependencies.
2. Run the unit tests and `python3 tools/check_release.py`.
3. For media/installer changes, run the optional synthetic-media smoke test on macOS.
4. Describe the trigger, resulting behavior, and verification in the pull request.
5. Do not submit real videos, machine-generated workflows/configuration, process
   logs, credentials, customer paths, or identifiable screenshots.

Use synthetic fixtures generated at test time. Read PRIVACY.md before sharing
diagnostics. Intel/macOS-version test results should state what was actually tested.
