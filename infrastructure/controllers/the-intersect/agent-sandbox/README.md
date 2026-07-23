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

## Operating notes (learned deploying `openclaw`)

- **The controller's default NetworkPolicy is unusable on this cluster.** For a
  managed sandbox it creates a policy that blocks `10.0.0.0/8` egress (kills
  cluster DNS — the agent can't resolve anything) and only allows ingress from a
  `sandbox-router` component that this install does **not** deploy. Set
  `spec.networkPolicyManagement: Unmanaged` on the `SandboxTemplate` and supply
  your own policy. See `apps/openclaw/the-intersect/ciliumnetworkpolicy.yaml` for
  a working example (Cilium `ingress` entity + DNS/world egress).
- **Editing a `SandboxTemplate` does not roll existing pods.** The WarmPool
  captured the template when it created its `Sandbox` objects; deleting the *pod*
  just recreates it from the stale spec. To apply template changes:
  `kubectl -n <ns> delete sandbox --all` (regenerates from the current template —
  and gives fresh `volumeClaimTemplates` PVCs).
- **Reaching a sandbox:** `kubectl port-forward` does not work with gVisor pods
  (the listener is in gVisor's netstack, not the host pod-netns loopback). Expose
  via a Service + HTTPRoute (see the openclaw app), not port-forward.

`apps/openclaw/the-intersect/` is the reference workload for all of the above.

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
