# arc

[Actions Runner Controller](https://github.com/actions/actions-runner-controller)
— GitHub Actions runners that execute inside `the-intersect`.

## Why

TimescaleDB lives at `10.0.0.10`, on the LAN. GitHub's hosted runners cannot
route there, so anything that has to touch the database — applying migrations,
checking the live schema for drift — could not be automated from Actions. A
runner in the cluster can reach it.

## Layout

| Namespace     | What                                                          |
| ------------- | ------------------------------------------------------------- |
| `arc-systems` | The controller and the Helm repository it is installed from.   |
| `arc-runners` | Runner pods and the GitHub credentials for each scale set.     |

Runner pods are kept out of the controller's namespace so a workflow cannot
reach the controller's service account, which can read the credentials for
every scale set rather than just its own.

## Security posture

A runner that can reach the database is a meaningful piece of trust, so it is
narrowed in three ways:

1. **Scoped to one repository**, not the `lfprocks` organisation. Adding a repo
   to the org does not grant it LAN access.
2. **Only reachable from `push` to `main`.** Workflows that run on
   `pull_request` stay on hosted runners, so code from an unmerged branch — or
   from a fork — never executes in the cluster. This is the important one:
   `runs-on: timescale-migrations` in a PR-triggered workflow would undo it.
3. **Ephemeral pods.** Every job gets a fresh runner that is destroyed
   afterwards, so nothing persists between jobs.

`containerMode: dind` runs a privileged sidecar, which is why `arc-runners` has
privileged pod security rather than baseline. That is what the first two
constraints are paying for — it is not a setting to copy onto an org-wide scale
set without thinking about it.

## Credentials

`github_secret.sops.yaml` holds a GitHub App's credentials and **must be filled
in before this is merged** — it is committed with placeholders.

Create the App at <https://github.com/settings/apps> (or under the `lfprocks`
org for an org-owned App):

- Repository permissions: **Administration: read & write**, **Metadata: read**,
  **Actions: read**
- No webhook needed
- Install it on `lfprocks/timescale-migrations` only

Then collect three values — the App ID, the Installation ID (from the
installation's URL, `.../installations/<id>`), and a generated private key —
and write them in:

```sh
$EDITOR infrastructure/controllers/shared/arc/github_secret.sops.yaml
./scripts/encrypt.sh infrastructure/controllers/shared/arc/github_secret.sops.yaml
```

A classic PAT with `repo` scope works instead, as a single `github_token` key,
but it is a broader and longer-lived credential than an App installation token
and is not what this is set up for.

## Checking it works

```sh
kubectl get autoscalingrunnerset -n arc-runners
kubectl get pods -n arc-runners
```

The scale set also appears under the repository's
**Settings → Actions → Runners** as a runner group named
`timescale-migrations`. With `minRunners: 0` there will be no pods until a job
is queued.
