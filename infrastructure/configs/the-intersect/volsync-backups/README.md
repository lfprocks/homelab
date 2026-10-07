# Backups and restores

| What | How | Where | Schedule / retention |
|---|---|---|---|
| Tier-1 Ceph PVCs (forgejo repos, outline, homebox, home-assistant, pocket-id, openclaw, zwavejs, flippydrive, emqx, immich photos, grafana) | VolSync restic, from a CSI snapshot (crash-consistent) | GCS `lfpr-p-gcsb-usw1-volsync-backups-szog-ea82375` (versioned, deleted objects recoverable 30 days) | daily 02:00-03:40, keep 7 daily / 4 weekly / 6 monthly |
| CNPG Postgres (forgejo, immich, outline, transcribe, paperless) | barman base backups + continuous WAL | GCS `cnpg-backups` | daily, 30 days, point-in-time |
| Everything on olympic's `main` pool (SMB cluster volumes, photos, media, files) | ZFS snapshots + local replication to `vault/backups/main` | olympic (terraform `truenas/snapshots.tf`) | daily 30d, weekly 12w |

Alerts (Grafana → Pushover): `VolSyncBackupOutOfSync`, `BackupStale` (CNPG).

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
