# cloudnative-pg

[CloudNativePG](https://cloudnative-pg.io/) **v1.25.1** — the operator behind
every Postgres cluster on `the-intersect`. This file documents the backup setup
and, more importantly, **how to restore from it**.

## Clusters and their backups

Every cluster archives WAL continuously and takes a nightly base backup into a
single GCS bucket, `gs://lfpr-p-gcsb-usw1-cnpg-backups-7q2v-60d5245`
(provisioned from [`lfprocks/infrastructure`](https://github.com/lfprocks/infrastructure),
`lfpcore/Pulumi.prod.yaml`).

| Cluster | Namespace | Image | Instances | Base backup |
| --- | --- | --- | --- | --- |
| `transcribe-postgres`  | `transcribe` | `postgresql:17`              | 1 | 02:30 |
| `immich-postgres-new`  | `immich`     | `cloudnative-pgvecto.rs:16.5-v0.3.0` | 2 | 03:15 |
| `outline-postgres`     | `outline`    | `postgresql:16`              | 2 | 03:30 |
| `forgejo-postgres`     | `forgejo`    | `postgresql:16`              | 2 | 03:45 |

Schedules are staggered so several clusters don't stream to GCS at once and
contend for ceph and network bandwidth. `retentionPolicy` is `30d` on all of
them, matching loki/mimir, and barman expires old backups — the bucket has no
lifecycle rule of its own.

`serverName` is left at its default on every cluster. That default is the
cluster name, and it is the only thing keeping their prefixes from colliding
inside the shared bucket root. **Do not set `serverName` explicitly** unless you
have thought about that.

The credential is the same GCP service-account key the LGTM stack uses,
re-encrypted into each namespace as the SOPS secret
`cnpg-backup-gcs-credentials`. There is no secret replication controller in this
cluster, so every namespace needs its own copy.

## Checking that backups are actually working

**`pg_stat_archiver.archived_count` is not evidence.** CNPG's `wal-archive`
exits 0 as a no-op when no object store is configured, so the counter includes
successes that wrote nothing, and `failed_count` can hold failures from days
earlier. Trust objects in the bucket instead:

```sh
# ground truth: real objects per cluster
gcloud storage ls -r gs://lfpr-p-gcsb-usw1-cnpg-backups-7q2v-60d5245/<cluster>/ \
  --project lfpr-p-core-8t9y
# note: gs://bucket/** returns nothing in gcloud; use -r

# CNPG's own view
kubectl -n <ns> get cluster <cluster> \
  -o jsonpath='{.status.conditions[?(@.type=="ContinuousArchiving")].status} {.status.firstRecoverabilityPoint}{"\n"}'
```

`firstRecoverabilityPoint` being set is the signal that PITR is possible.

## Restoring

Recovery builds a **new** cluster from the archive; it never writes into an
existing one. The usual shape is: restore into a scratch cluster, confirm the
data, then decide whether to promote it or copy data out.

> **The one rule that matters: a recovery cluster must NOT have a `backup:`
> section.** If it does, it will archive WAL back into the source's own path and
> corrupt the very backup you are restoring from. Omitting it is what makes this
> safe to run against a live system.

Save this as `restore-test.yaml` and adjust the four marked values:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: restore-test                 # scratch name, must not collide
  namespace: transcribe              # namespace holding cnpg-backup-gcs-credentials
spec:
  instances: 1
  imageName: ghcr.io/cloudnative-pg/postgresql:17   # MUST match the source major version
  # Only needed in kyverno opt-in namespaces; see the require-labels policy.
  inheritedMetadata:
    labels:
      k8s.lfp.rocks/service: "restore-test"
      k8s.lfp.rocks/version: "17"
  storage:
    size: 10Gi
    storageClass: ceph-block
  # NO `backup:` block here. See the warning above.
  bootstrap:
    recovery:
      source: source-cluster
      # For PITR, add:
      # recoveryTarget:
      #   targetTime: "2026-07-30 03:50:00.000000+00"
  externalClusters:
    - name: source-cluster
      barmanObjectStore:
        destinationPath: gs://lfpr-p-gcsb-usw1-cnpg-backups-7q2v-60d5245
        serverName: transcribe-postgres        # the cluster being restored FROM
        googleCredentials:
          applicationCredentials:
            name: cnpg-backup-gcs-credentials
            key: service-account.json
        wal:
          compression: gzip
```

```sh
kubectl apply -f restore-test.yaml

# follow it: expect "Target backup found" -> "Restore completed"
# -> "consistent recovery state reached" -> WAL replay
kubectl -n <ns> logs <cluster>-1-full-recovery-<hash> --all-containers -f
```

### Verifying the restore

Row counts alone can mislead — a schema can restore while its contents are
wrong. Compare a checksum over serialised rows instead:

```sh
# run against BOTH the source and the restored cluster; the hashes must match
kubectl -n <ns> exec <pod> -c postgres -- psql -U postgres -d <db> -tAc "
  select md5(string_agg(t::text, ',' order by t::text))
  from (select * from <table> order by 1 limit 100) t;"
```

### Cleaning up

```sh
kubectl -n <ns> delete cluster restore-test    # takes its pods and PVC with it
```

Then confirm the source path was not contaminated — there should be no new
prefix in the bucket and the source's base backup count should be unchanged.

## Things worth knowing before you need them

- **Recovery time scales with WAL volume, not database size.** Each segment is a
  separate GCS fetch and decompress, measured at roughly 12s each. A small
  database with a lot of accumulated WAL still takes a long time. More frequent
  base backups shorten replay; that is the lever, not backup size.
- **A recovering cluster accepts read-only connections before it finishes**, from
  consistent-recovery-state onward. If you need data urgently rather than
  completely, you can query it mid-replay.
- **The image major version must match the source.** Recovery into a different
  major version will not work; use the same `imageName` the source cluster uses.
- **Deleting a `Cluster` deletes its PVC.** The backup is the only copy after
  that, and only for the 30-day retention window.

## Recovering a hopelessly-lagging replica

Not a restore, but the neighbouring failure. If a replica asks for a WAL segment
the primary has already recycled, it can never catch up:

```
FATAL: could not receive data from WAL stream:
  ERROR: requested WAL segment 0000000... has already been removed
```

Check `pg_replication_slots` — if the slot shows `active=f` with a NULL
`restart_lsn` it is reserving nothing and will not save the replica. With WAL
archiving in place the replica can now recover from the archive. If it is still
stuck, delete its PVC and pod and let CNPG re-clone it with `pg_basebackup`:

```sh
kubectl -n <ns> delete pvc <cluster>-<n> --wait=false
kubectl -n <ns> delete pod <cluster>-<n>
```

CNPG creates a `-join-` job, clones from the primary, and brings up a fresh
instance (numbered one higher). The primary is untouched.
