# Forgejo Actions runners

There is one `forgejo-runner` (capacity 3) with a Docker-in-Docker sidecar, plus a
rootless `buildkitd` for image builds. The runner is registered instance-wide, so every
repository and org can use it.

| `runs-on:` | Job image |
|---|---|
| `ubuntu-latest`, `ubuntu-24.04` | `ghcr.io/catthehacker/ubuntu:act-24.04` (GitHub-runner-like) |
| `ubuntu-22.04` | `ghcr.io/catthehacker/ubuntu:act-22.04` |
| `docker` | `node:22-bookworm` |

`uses: actions/checkout@v4` resolves on github.com (`DEFAULT_ACTIONS_URL=github`).
Pin third-party actions by SHA.

## Security model

- **Jobs:** unprivileged containers inside DinD, with **no Docker socket**,
  no host network and no volume mounts. Each is capped at 2 CPU, 4 GiB and 4096 pids.
- **DinD:** privileged, which upstream says it must be. The jobs it runs are not.
  The residual risk is that a container-escape exploit inside a job gives root on that node.
- **Network:** `network-policy.yaml` lets jobs and builds reach the public
  internet only. That includes Forgejo and its registry, because pods resolve the
  Forgejo hostname to the public address. Cluster pods and services, the LAN
  and the nodes are all unreachable.
- **Registration:** only `forgejo-runners` can register. The registration secret
  lives in `runner-registration.sops.yaml` and the uuid is never in git.

## Building and pushing images

Jobs have no Docker daemon. Build through BuildKit, and push with the job token
(or a `package`-scoped token secret):

```yaml
jobs:
  image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Use the cluster BuildKit
        run: docker buildx create --use --driver remote tcp://buildkitd.forgejo-runners.svc.cluster.local:1234
      - name: Log in to the Forgejo registry
        run: echo "${{ secrets.GITHUB_TOKEN }}" | docker login "${GITHUB_SERVER_URL#https://}" -u "${{ github.actor }}" --password-stdin
      - name: Build and push
        run: docker buildx build --push -t "${GITHUB_SERVER_URL#https://}/${GITHUB_REPOSITORY,,}:${GITHUB_SHA::12}" .
```

## Registration

Registration is done once on the server, and again after a database restore.
`forgejo-cli actions register` is idempotent for the same secret.

```bash
cd ~/go/src/github.com/lfprocks/homelab && export SOPS_AGE_KEY_FILE=$PWD/age.agekey
S=$(sops -d --extract '["stringData"]["secret"]' apps/forgejo-runners/the-intersect/runner-registration.sops.yaml)
kubectl -n forgejo exec deploy/forgejo -c forgejo -- \
  forgejo forgejo-cli actions register --name cluster-runner --secret "$S"
unset S
```

**To rotate the secret:**
1. Generate a new 40-hex secret, uuid and token into the sops file. The uuid is the
   hex of the secret's first 16 characters, formatted 8-4-4-4-12.
2. Re-run the register command above.
3. Restart the runner.

## Capacity

To add capacity, raise `runner.capacity` and the DinD memory limit together:
jobs × 4 GiB, plus about 2 GiB of headroom.
