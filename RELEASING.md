# Publishing a ClipFitTT source release

1. Review VERSION, CHANGELOG.md, README.md, DESCRIPTION.txt, COPYRIGHT, and THIRD_PARTY_NOTICES.md.
2. Run unit tests and the optional macOS smoke test.
3. Run `python3 tools/check_release.py`, then inspect `git diff --cached` and
   `git diff --cached --stat`. Ignore rules do not remove files already tracked.
4. Confirm your intended public Git author name/email. Use the exact noreply
   address shown by GitHub in Settings → Emails if desired. Do not invent one.
5. Commit the reviewed source. This repository intentionally requires an
   explicitly configured Git identity; initialization alone creates no commit.
6. On GitHub, create an empty repository. Do not pre-create a second README,
   LICENSE, or .gitignore if importing this local repository.
7. Add the remote and push only after reviewing the repository visibility and
   commit identity. The following values are placeholders:

```sh
git config --local user.name 'YOUR_PUBLIC_NAME'
git config --local user.email 'YOUR_GITHUB_NOREPLY_EMAIL'
git add .
git diff --cached --stat
git diff --cached
git commit -m 'Initial source release v1.0.0'
git remote add origin https://github.com/YOUR_ACCOUNT/clipfittt.git
git push -u origin main
```

Optional release tag, after the initial push:

```sh
git tag -a v1.0.0 -m 'Source release v1.0.0'
git push origin v1.0.0
git archive --format=zip --prefix=clipfittt-1.0.0/ \
  --output=../clipfittt-1.0.0-source.zip v1.0.0
```

Publish source archives only. Do not attach locally downloaded HandBrakeCLI,
FFmpeg binaries, DMGs, logs, generated workflows, runtime configuration, videos,
or a copy of the whole installed application directory. Redistributing an
external executable introduces separate upstream license/source obligations.

GitHub Actions runs the offline unit/privacy checks for pushes and pull requests.
Local smoke-test output is not proof of Intel or other untested macOS support.
