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

Create the App under the `lfprocks` organisation, at
<https://github.com/organizations/lfprocks/settings/apps/new>:

- **Repository permissions → Administration: Read and write.** This is the one
  that lets ARC register and remove runners. It is required only because the
  scale set is scoped to a repository; an organisation-scoped scale set would
  use **Organization permissions → Self-hosted runners: Read and write**
  instead, and would not need Administration at all.
- **Repository permissions → Metadata: Read-only.** GitHub selects this
  automatically once Administration is set.
- Nothing else. Older ARC guides also list `Actions: read` and `Checks: read`;
  those belong to the pre-scale-set controller and its webhook-driven scaling.
  Runner scale sets long-poll instead, so neither is needed here.
- Uncheck **Active** under Webhook — there is no webhook to receive.
- Set the App to **"Only on this account"**.

Install it on `lfprocks/timescale-migrations` **only** — "Only select
repositories", not "All repositories". That repository pin is half of what
keeps a LAN-reachable runner from becoming an organisation-wide one.

Then collect three values and write them in:

| Value | Where |
| --- | --- |
| App ID | the App's own settings page, near the top |
| Installation ID | the trailing number in `https://github.com/organizations/lfprocks/settings/installations/<id>` after installing |
| Private key | **Generate a private key** at the bottom of the App page — downloads a `.pem`, and is shown only once |

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
