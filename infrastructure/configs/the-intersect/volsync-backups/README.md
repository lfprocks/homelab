# Backups and restores

| What | How | Where | Schedule / retention |
|---|---|---|---|
| Tier-1 Ceph PVCs (forgejo repos, outline, homebox, home-assistant, pocket-id, openclaw, zwavejs, flippydrive, emqx, immich photos, grafana) | VolSync restic, from a CSI snapshot (crash-consistent) | GCS `lfpr-p-gcsb-usw1-volsync-backups-szog-ea82375` (versioned, deleted objects recoverable 30 days) | daily 02:00-03:40, keep 7 daily / 4 weekly / 6 monthly |
| CNPG Postgres (forgejo, immich, outline, transcribe, paperless) | barman base backups + continuous WAL | GCS `cnpg-backups` | daily, 30 days, point-in-time |
| Everything on olympic's `main` pool (SMB cluster volumes, photos, media, files) | ZFS snapshots + local replication to `vault/backups/main` | olympic (terraform `truenas/snapshots.tf`) | daily 10:00 UTC (30d), weekly Sun 10:15 UTC (12w) |
| Forgejo package registry (container images) | rclone sync of the `forgejo-packages` RGW bucket (`registry-backup` CronJob) | GCS `lfpr-p-gcsb-usw1-registry-backups-r7g2-8a62ba4/forgejo-packages` (versioned, deleted/overwritten objects kept 30 days) | daily 03:50 UTC |

Alerts (Grafana → Pushover): `VolSyncBackupOutOfSync`, `BackupStale` (CNPG),
`RegistryBackupStale` (registry, last success > 26h).

Everything restic needs is in the sops files here (one shared repository
password + the GCS key). Decrypting them needs `age.agekey`: **without that
key, no backup can be restored** — keep a copy of it off this cluster.

## Restore a PVC from VolSync (tested 2026-10-07: outline-data, byte-identical)

1. Create an empty PVC to restore into (or scale the app to 0 and restore
   into its own PVC with `copyMethod: Direct`).
2. In the PVC's namespace (where `restic-<pvc>` lives):

   ```yaml
   apiVersion: volsync.backube/v1alpha1
   kind: ReplicationDestination
   metadata: {name: restore-<pvc>}
   spec:
     trigger: {manual: restore-1}
     restic:
       repository: restic-<pvc>
       destinationPVC: <target-pvc>
       copyMethod: Direct
       # restoreAsOf: "2026-10-01T00:00:00Z"   # optional point in time
       cacheCapacity: 1Gi
       cacheStorageClassName: ceph-block
       moverSecurityContext: {runAsUser: <app uid>, runAsGroup: <gid>, fsGroup: <gid>, seccompProfile: {type: RuntimeDefault}}
       moverPodLabels: {k8s.lfp.rocks/service: volsync-mover, k8s.lfp.rocks/version: "0.16.0"}
       moverResources: {requests: {cpu: 50m, memory: 128Mi}}
   ```

3. Wait for `.status.lastManualSync == restore-1` and
   `.status.latestMoverStatus.result == Successful`, then point the app at
   the restored PVC (or scale it back up). Delete the ReplicationDestination.

## Restore a CNPG database

Create a new Cluster with `bootstrap.recovery` from the `cnpg-backups`
barman object store (`serverName` = the original cluster name), optionally
with a `recoveryTarget.targetTime`. See the CNPG "Recovery" docs; never
recover into the running cluster's name.

## Restore from olympic ZFS

Snapshots are browsable read-only under `<dataset>/.zfs/snapshot/` on
olympic (snapdir is hidden but accessible), or roll back / clone in the
TrueNAS UI. If the `main` pool is lost, `vault/backups/main` holds a
read-only replica: clone it or replicate it back.

## Restore the Forgejo package registry

A registry is two halves that must match: the **index** (which tags point at
which blobs) lives in the forgejo Postgres database, the **blobs** live in the
`forgejo-packages` bucket. Blobs are content-addressed and never rewritten, so
restoring the database to time *T* only needs every blob that existed at *T*.
The GCS bucket keeps deleted objects 30 days, matching CNPG's 30-day
point-in-time window, so any *T* in that window is recoverable.

Not yet drilled with real images (the registry was empty when this was
written); do a drill once it holds something worth losing.

**Bucket lost, database fine** (most likely case):

1. If the claim was deleted, re-apply it; `ceph-bucket-retain` and the fixed
   `bucketName` bind it back to the same bucket if it still exists.
2. Copy the backup back. Run from a pod in `forgejo` with the same env as the
   `registry-backup` CronJob (both remotes need `no_check_bucket=true`):

   ```sh
   rclone copy gcs:lfpr-p-gcsb-usw1-registry-backups-r7g2-8a62ba4/forgejo-packages \
     rgw:forgejo-packages --size-only --fast-list --transfers 8 -v
   ```

   `copy`, not `sync`: never delete from the live bucket during a restore.
3. Suspend the `registry-backup` CronJob until the bucket is complete, so its
   wiped-bucket guard does not have to save you.

**Database restored to time T** (CNPG recovery, see above): also bring back
blobs deleted after *T*. They are noncurrent versions in GCS:

```sh
B=gs://lfpr-p-gcsb-usw1-registry-backups-r7g2-8a62ba4
gcloud storage ls --all-versions "$B/forgejo-packages/**"  # name#generation, noncurrent included
# copy the newest generation of each object missing from the live bucket back,
# then rclone copy GCS -> RGW as above
```

**Verify:** `crane ls <forgejo-host>/<owner>/<image>` and pull one image by
digest (`crane blob ... | sha256sum` must equal the digest).

Operational details (storage config, cleanup rules, quota, pull credentials,
known pitfalls): `apps/forgejo/the-intersect/REGISTRY.md`.

