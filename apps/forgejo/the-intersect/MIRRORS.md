# GitHub mirrors

Forgejo pull-mirrors every non-fork GitHub repository of `michaelpeterswa` and of
these orgs: alpineworks, kcesar, lfprocks, nwsocial, rattlesnakemountain,
redoubtapp, searchandrescuegg, trailheads-io (Kochava and HyperspeedOne are
deliberately excluded). GitHub stays the source of truth. Mirrors sync hourly
(`[mirror] DEFAULT_INTERVAL`) and are read-only in Forgejo. To move a repo's
development to Forgejo, convert that one repo to a regular repo and, if wanted,
push-mirror it back to GitHub.

As of 2026-10-07: 307 mirrors (267 public, 40 private). kcesar's one private
repo is not mirrored yet (no token).

## Adding repos

`apps/forgejo/scripts/mirror-github.sh` is idempotent. Re-run it to pick up new
repos; existing ones are skipped:

```bash
cd ~/go/src/github.com/lfprocks/homelab
apps/forgejo/scripts/mirror-github.sh michaelpeterswa kcesar trailheads-io searchandrescuegg \
  alpineworks lfprocks nwsocial rattlesnakemountain redoubtapp
```

- **Repo lists** come from your `gh` login, which is never stored.
- **Public repos** use a plain git mirror (`service: git`) with no credential.
- **Private repos** use `service: github` with that owner's token, and prompt
  for it. Press Enter to skip an owner's private repos. A new org needs its own
  token, plus `apps/forgejo-runners/scripts/provision-org-publisher.sh <org>` for CI.

## Private-repo tokens

These are fine-grained GitHub tokens, **one per owner**: a fine-grained token
covers only one resource owner, and GitHub has no API to create them.

- **Resource owner:** that user or org.
- **Repository access:** All repositories.
- **Permissions:** Contents **read-only** (Metadata read is implied).
- **Name:** `forgejo-mirror-<owner>`. Expiry is the account maximum; the first set expires around **2027-10-07**.

Form shortcut (fields may need checking):
`https://github.com/settings/personal-access-tokens/new?name=forgejo-mirror-<owner>&target_name=<owner>&contents=read&expires_in=365`

**Where they live:** Forgejo keeps a pull mirror's credential in plaintext in
that repo's git remote URL, on the Forgejo volume and therefore in its VolSync
backups. That is why they are read-only and per owner. Verify that only private
mirrors carry one: the count of remotes containing `@github.com` must equal the
number of private mirrors.

### Renewal (before expiry)

When a token expires, that owner's private mirrors **stop syncing silently**.

1. Create new tokens as above.
2. Update each private mirror's credential. Either:
   - per repo: Settings → Repository → Mirror settings → Authorization; or
   - in bulk: delete that owner's private mirrors and re-run the script with
     the new token. They are mirrors, so nothing is lost.
3. Revoke the old tokens on GitHub.

## Lessons from the first run

- The migrate API is synchronous. Through the public gateway, slow clones hit a
  504, and a dropped request can leave a repo half-migrated (`status` 1). It
  usually completes server-side anyway. The script therefore calls the API with
  `kubectl exec` inside the Forgejo pod, and reports not-ready repos instead of
  deleting them.
- `service: github` without a token calls the GitHub API anonymously (60/h) and
  then sleeps until the reset, giving about 30 repos per hour. Public repos use
  `service: git`.
- Forgejo cannot archive a mirror (422), so archived GitHub repos are mirrored
  unflagged.
- Before mirroring, the volume held 228 orphaned repos from an earlier
  instance. They blocked migrations (409), and their remotes embedded a live
  classic token, which was revoked through GitHub's credential revocation API.
  Admin → Unadopted repositories (`/api/v1/admin/unadopted`) is the tool for that.
