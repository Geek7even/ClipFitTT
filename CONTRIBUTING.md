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

For logging changes, cover bounded storage, privacy field filtering, missing
or failing native backends, concurrent pipe draining, and cancellation.
Run `python3 tools/logging_smoke_test.py` on macOS when a compiler and log
stream access are available. It uses synthetic events and must not change
system logging settings. Generated native libraries are local build artifacts;
publish the bridge source, not the compiled library. Keep installation usable
without a compiler and avoid adding mandatory pip dependencies.

Before every GitHub push, including documentation-only changes, complete the
mandatory privacy review in AGENTS.md. Sanitize the entire outgoing commit
range and publication assets, then rescan; reviewing only the working tree is
insufficient. Ask the maintainer about uncertain private/public information.

For project descriptions, README footers, release notes, and any future About
view, follow the standard project credits in RELEASING.md.

## Comments and docstrings

- Follow [PEP 8](https://peps.python.org/pep-0008/#comments) for comments and
  [PEP 257](https://peps.python.org/pep-0257/) for docstring structure.
- Keep code documentation in English, matching the existing source files.
- Give modules and public callables a concise summary; describe input/output
  contracts, side effects, error behavior, and caller restrictions when useful.
- Explain decisions, units, boundary conditions, and recovery assumptions in
  comments rather than narrating obvious statements. Update comments with code.
- Prefer descriptive test names plus fixture/scenario notes over repeating
  each test's implementation in a docstring.
