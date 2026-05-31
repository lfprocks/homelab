# Archived clusters

Clusters in this directory are **deprecated**. Their manifests are kept here to
preserve history and configuration for reference, but they are **not** active
Flux targets and should **not** be bootstrapped as-is.

Nothing under `clusters/_archive/` is reconciled. Each archived cluster was
bootstrapped independently against its own `./clusters/<name>` path, so leaving
these files here has no effect on the live [`the-intersect`](../the-intersect)
cluster.

| Cluster          | Status     | Entrypoint                                |
| ---------------- | ---------- | ----------------------------------------- |
| `libvirt-unraid` | Deprecated | [`libvirt-unraid/`](./libvirt-unraid)     |
| `solar-pi`       | Deprecated | [`solar-pi/`](./solar-pi)                 |

## What was preserved

Only each cluster's **entrypoint** (`clusters/<name>/`) was archived. The
cluster-specific overlays they referenced are intentionally left in place and
are inert unless a cluster points at them:

- `infrastructure/configs/<name>/`
- `infrastructure/controllers/<name>/` (libvirt-unraid only)
- `apps/<app>/<name>/` overlays — e.g. `apps/ollama/libvirt-unraid`,
  `apps/shallenge-miner/libvirt-unraid`, `apps/nws-forecast-summarizer/libvirt-unraid`,
  `apps/letterkenny-api/solar-pi`, `apps/weatherlinklive-timescaledb-inserter/solar-pi`.

## Restoring a cluster

1. Move the directory back up to `clusters/<name>/`.
2. The committed `flux-system/gotk-sync.yaml` still references the original
   `./clusters/<name>` path; re-run `flux bootstrap` against that path to
   regenerate Flux's own components and re-establish the Git sync on a live
   cluster.
