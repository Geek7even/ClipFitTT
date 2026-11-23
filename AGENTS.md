# Mandatory GitHub pre-push privacy review

Before every GitHub push, including documentation-only changes and tags:

1. Establish the intended remote/ref and the complete outgoing commit range.
2. Review outgoing file versions, diffs, author/committer/tag identities, and
   any release assets or notes included in the publication operation.
3. Check for passwords, tokens, API/private keys, credential-bearing URLs,
   private email/contact information, real user or NAS paths, customer names,
   logs, generated configuration/workflows, recovery data, and real media.
4. Run `python3 tools/check_release.py` and manually review content and history;
   that checker is a guard and does not replace the full outgoing review.
5. Remove sensitive material from the actual outgoing commits/assets and
   repeat the checks. Deleting only a working-tree file is insufficient if
   another outgoing commit still contains it. Ask about ambiguous information;
   retain public attribution already explicitly approved by the user.
6. Push only after findings are resolved. Do not force-push or rewrite already
   published history without explicit user authorization.
7. Verify the remote result and state the privacy-review outcome in the reply.

Do not print discovered credential values in tool output or reports. Preserve
already-published versioned release archives/tags unless a change is explicitly
authorized. These instructions do not independently authorize a push.
