# agent-sandbox

[kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
— a SIG Apps controller providing the `Sandbox` CRD (and the `SandboxTemplate` /
`SandboxClaim` / `SandboxWarmPool` extensions) for **isolated, stateful, singleton
workloads** such as long-running AI agent runtimes: stable identity, persistent
storage, lifecycle controls (pause/resume/scheduled deletion), and strong
isolation via a gVisor/Kata RuntimeClass.

## What this installs

- Vendored upstream release manifest, pinned to **v0.5.2**
  (`sandbox-with-extensions.yaml`): namespace `agent-sandbox-system`, the 4 CRDs,
  RBAC, and the controller Deployment
  (`registry.k8s.io/agent-sandbox/agent-sandbox-controller:v0.5.2`).
- Wired into Flux via `infrastructure/controllers/the-intersect/kustomization.yaml`.

## gVisor prerequisite

A Sandbox opts into gVisor with a single field:

```yaml
spec:
  podTemplate:
    spec:
      runtimeClassName: gvisor
```

This requires the `gvisor` RuntimeClass + the gVisor system extension on the
worker nodes. **Both are already in place on the-intersect** (gVisor was rolled
onto all four workers via Talos Image Factory; the `gvisor` RuntimeClass is
applied cluster-wide). See the Talos repo `the-intersect/gvisor/` for that setup.

> Follow-up worth considering: the `gvisor` RuntimeClass is currently applied
> out-of-band (kubectl), not Flux-managed. Moving it into this repo (like
> `nvidia/runtimeclass.yaml`) would make the dependency fully GitOps-tracked.

## Smoke test (manual)

`smoke-test/sandbox-gvisor.yaml` is a minimal gVisor-isolated Sandbox. It is
**not** part of the kustomization (won't be reconciled by Flux) — apply it by
hand once the controller is Running, then delete it:

```bash
kubectl apply -f smoke-test/sandbox-gvisor.yaml
kubectl get pods -n default -l agents.x-k8s.io/sandbox=gvisor-smoketest -o wide
kubectl logs  -n default -l agents.x-k8s.io/sandbox=gvisor-smoketest
#   -> "Linux ... 4.4.0 ..." (gVisor's emulated kernel) confirms the sandbox
kubectl delete -f smoke-test/sandbox-gvisor.yaml
```

## Upgrading

Re-download the release asset for the new tag and replace the vendored file:

```bash
curl -sL https://github.com/kubernetes-sigs/agent-sandbox/releases/download/vX.Y.Z/sandbox-with-extensions.yaml \
  -o sandbox-with-extensions.yaml
```

> Note: agent-sandbox is pre-1.0 (`agents.x-k8s.io/v1beta1`) — expect API churn
> between releases; read the upstream changelog before bumping.
