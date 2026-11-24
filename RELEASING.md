# Publishing a ClipFitTT source release

Current release: **ClipFitTT Beta 0.9.3**, technical version **0.9.3-beta.1**.
Treat this package as a pre-release. The complete version is read from VERSION;
the workflow's numeric Apple version field uses 0.9.3, with the complete beta
identifier retained separately in ClipFitTTVersion.

Every GitHub push requires the privacy review in AGENTS.md, even for small
documentation updates. Inspect the complete outgoing history, commit/tag
identity, and publication assets; remove sensitive content and recheck before
pushing. Resolve ambiguous public/private information with the user first.

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
git commit -m 'Prepare ClipFitTT 0.9.3-beta.1'
git remote add origin https://github.com/YOUR_ACCOUNT/clipfittt.git
git push -u origin main
```

Optional release tag, after the initial push:

```sh
git tag -a v0.9.3-beta.1 -m 'ClipFitTT Beta 0.9.3'
git push origin v0.9.3-beta.1
git archive --format=zip --prefix=clipfittt-0.9.3-beta.1/ \
  --output=../clipfittt-0.9.3-beta.1-source.zip v0.9.3-beta.1
```

If the local tag already exists, verify its target before pushing it; do not
recreate or overwrite it. Existing local development history is retained.

Publish source archives only. Do not attach locally downloaded HandBrakeCLI,
FFmpeg binaries, DMGs, logs, generated workflows, runtime configuration, videos,
or a copy of the whole installed application directory. Redistributing an
external executable introduces separate upstream license/source obligations.

GitHub Actions runs the offline unit/privacy checks for pushes and pull requests.
Local smoke-test output is not proof of Intel or other untested macOS support.

## Standard project credits

Use these exact credits in the README footer, release notes, and an About view
if the project gains one:

```markdown
**Designed by Geek7even**

**Developed entirely with AI**
```

In plain-text fields such as the GitHub repository description, use
`Designed by Geek7even · Developed entirely with AI` without Markdown markers.
Keep the functional project introduction before the credits. Preserve the legal
COPYRIGHT/LICENSE information and third-party acknowledgements independently.
Carry this convention forward in future releases; prepare a new version before
replacing any already-published versioned archive or tag.

## Version announcements

When asked to upgrade the version, first read VERSION and report the current
project version. Recommend an appropriate next version with a brief reason,
then ask the user to select or confirm it. Wait for that explicit selection
before changing version files, renaming release folders, committing/tagging
a release, or building its package. The request to upgrade alone is not a
selection of the recommendation. Distinguish the installed version from the
project version when they differ.

After selection, report the old/new display names, complete technical version,
prerelease identifier, and Git tag in the completion report. Keep beta releases
marked as prereleases. For reference, this package is ClipFitTT Beta 0.9.3,
`0.9.3-beta.1`, with identifier `beta.1` and tag `v0.9.3-beta.1`.
A version bump alone does not authorize a GitHub push or local deployment.
