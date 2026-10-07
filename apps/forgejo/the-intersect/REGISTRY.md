# Forgejo package registry

Container images (and other packages) for every owner: your user plus the
mirrored GitHub orgs. Images are addressed as `<forgejo-host>/<owner>/<image>:<tag>`.

| Piece | Where |
|---|---|
| Blob storage | Ceph RGW bucket `forgejo-packages` (`objectbucketclaim.yaml`, class `ceph-bucket-retain`, so deleting the claim keeps the data) |
| Index | forgejo CNPG database |
| Off-site backup | `registry-backup` CronJob, 03:50 UTC → GCS, alert `RegistryBackupStale` (restore: `infrastructure/configs/the-intersect/volsync-backups/README.md`) |
| Cleanup | `package-cleanup-rules` CronJob (23:30 UTC) gives every owner the standard rule; Forgejo's own cron applies rules at midnight |
| Quota | `[quota] DEFAULT_GROUPS = packages-default`: 100 GiB packages per owner |
| Cluster pulls | Kyverno `forgejo-registry-pull` (`infrastructure/configs/the-intersect/kyverno/`) |

## Pushing and pulling

- **People:** `docker login <forgejo-host>` with your username plus an access token that has the `package` scope (Settings → Applications). There are no passwords; sign-in is Pocket ID only (`ACCESS.md`).
- **CI:** Forgejo Actions' job token.
- **Cluster:** nothing to do. Every namespace has the `forgejo-registry-pull` Secret, and pods using a Forgejo image get it injected.

## Cleanup rule

For every owner, the rule keeps:
- the 10 newest versions of each image;
- anything matching `^(v?[0-9].*|latest|main)$`;

and removes other versions after 14 days.

The CronJob only inserts the rule for owners that have none (`ON CONFLICT DO NOTHING`), so a rule edited in the UI (owner Settings → Packages) stays as you set it.

## Quota group (lives in the database, not in git)

To recreate it after a database loss, use the admin API. Get a temporary admin token as described in `ACCESS.md`, then:

```sh
curl -X POST -H "Authorization: token $TOKEN" -H 'Content-Type: application/json' \
  https://<forgejo-host>/api/v1/admin/quota/groups -d '{
  "name": "packages-default",
  "rules": [
    {"name": "packages-100gib", "limit": 107374182400, "subjects": ["size:assets:packages:all"]},
    {"name": "unlimited-all",   "limit": -1,           "subjects": ["size:all"]}
  ]}'
```

**Never drop the `unlimited-all` rule.** Forgejo denies any subject that no rule in an owner's groups matches, so a packages-only group blocks every git push, upload and attachment. Check the group with `GET /api/v1/user/quota/check?subject=size:repos:all`, which should return `true`.

## Rotating the cluster pull token

The bot user is `cluster-pull`; its token is `cluster-image-pull` with scope `read:package`.

1. `forgejo admin user generate-access-token --username cluster-pull --token-name cluster-image-pull-<date> --scopes read:package --raw`
2. Put the new token into `infrastructure/configs/the-intersect/kyverno/forgejo-registry-pull.sops.yaml` (a dockerconfigjson with `username` and `password` set to the token, and `auth` set to base64 of `cluster-pull:<token>`), then merge. Kyverno syncs it to every namespace.
3. Delete the old token.

## Pitfalls already hit

- **Ceph 20.2.4 rejects Forgejo's uploads.** The CVE-2026-54330 fix rejects the unsigned `Content-Type` that minio-go sends, and uploads fail with 500 "permission denied". The workaround, `rgw_sigv4_insecure`, is set in the rook-ceph-cluster HelmRelease. **Remove it** once Rook runs a Ceph release containing the Tentacle backport (tracker #79725).
- **`app.ini` merges.** The chart keeps `app.ini` on the PVC and merges values into it, so deleting a key from `values.yaml` leaves it in force. To undo a setting, set it back explicitly.
- **Booleans in `gitea.oauth`.** The chart renders them as a separate `"true"` argument, which the CLI rejects, and the init container crash-loops. Avoid boolean OAuth options, and test rendered flags against `forgejo admin auth update-oauth --id 999999 ...` first.
- **rclone against an OBC bucket.** It needs `no_check_bucket=true`; Rook's per-claim user may own only one bucket, so `CreateBucket` gets `TooManyBuckets`.
- **Flux substitution.** Any manifest with shell `${...}` needs `kustomize.toolkit.fluxcd.io/substitute: disabled`. It once turned `${n:-0}` into `0` and silently disabled the backup's wiped-bucket guard.
- **Debugging RGW auth.** Run `ceph config set client.rgw.ceph.objectstore.a debug_rgw 20/20` briefly, grep the RGW log by thread id (request pointers are reused), then set it back to `1/5`. Never run `radosgw-admin` without `--rgw-realm/--rgw-zone ceph-objectstore`: it silently creates `default.rgw.*` pools, which triggered `TOO_MANY_PGS`.
