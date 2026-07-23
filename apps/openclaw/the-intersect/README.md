# openclaw

[OpenClaw](https://github.com/openclaw/openclaw) run inside the **Agent Sandbox**
under **gVisor** on the-intersect, following the upstream
[`examples/openclaw-gvisor-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/examples/openclaw-gvisor-sandbox)
reference (SandboxTemplate + SandboxWarmPool + SandboxClaim), adapted for this
GitOps repo.

## Shape

- `sandboxtemplate.yaml` — `SandboxTemplate` with `runtimeClassName: gvisor`,
  init container seeding config, `openclaw` gateway
  (`ghcr.io/openclaw/openclaw:2026.3.23`, pinned), and a **`volumeClaimTemplates`**
  5Gi `ceph-block` workspace at `/workspace/.openclaw`. Hardened (non-root 1000,
  drop ALL caps, no privesc, seccomp RuntimeDefault; `readOnlyRootFilesystem:
  false` as OpenClaw requires). Token + `ANTHROPIC_API_KEY` come from the secret.
- `sandboxwarmpool.yaml` — one pre-warmed sandbox.
- `sandboxclaim.yaml` — adopts a sandbox and stamps `sandbox.users.io/openclaw-claim`
  on its pod so the Service targets exactly the claimed sandbox.
- `configmap.yaml` — minimal `openclaw.json` (Control UI allowed origins; bind/port/auth
  come from the gateway CLI flags).
- `service.yaml` — ClusterIP `openclaw-gateway:18789` selecting the claim label.
- `openclaw-secret.yaml` — SOPS-encrypted `openclaw-provider-keys`
  (`OPENCLAW_GATEWAY_TOKEN` generated; `ANTHROPIC_API_KEY` is a **placeholder**).

Namespace in `clusters/the-intersect/namespaces.yaml`; Flux Kustomization
`apps-openclaw` in `clusters/the-intersect/apps.yaml`.

Validated with server-side dry-run against the installed `agents.x-k8s.io` CRDs.

## Before it works: set the API key

The committed secret ships a placeholder key. Inject your real one (reuse the key
already in `transcribe-secret`, or a new one) and re-commit:

```bash
sops set apps/openclaw/the-intersect/openclaw-secret.yaml \
  '["stringData"]["ANTHROPIC_API_KEY"]' '"sk-ant-..."'
```

## Access (after it's Running)

```bash
# the claim's pod is labelled sandbox.users.io/openclaw-claim=openclaw-sandbox-claim
kubectl -n openclaw port-forward svc/openclaw-gateway 18789:18789
# token: kubectl -n openclaw get secret openclaw-provider-keys \
#   -o jsonpath='{.data.OPENCLAW_GATEWAY_TOKEN}' | base64 -d
open http://localhost:18789
```

## Notes / follow-ups

- **Model:** now the full Sandbox pattern (Template/WarmPool/Claim), matching the
  upstream example — pause/resume, warm-pool pre-warming, stable identity.
- **Image** pinned to `2026.3.23` (the example's tag); bump via Renovate.
- **`--allow-unconfigured`** lets the gateway start before a provider is set; it
  works once `ANTHROPIC_API_KEY` is populated.
- **External access:** ClusterIP + port-forward for now. For remote UI access add
  an HTTPRoute on the shared gateway (mind auth/TLS for an agent UI).
