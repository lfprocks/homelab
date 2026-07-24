# openclaw

[OpenClaw](https://github.com/openclaw/openclaw) — a long-running personal AI
agent — running inside the [Agent Sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
under **gVisor** on the-intersect. Follows the upstream
[`examples/openclaw-gvisor-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/examples/openclaw-gvisor-sandbox)
(SandboxTemplate + SandboxWarmPool + SandboxClaim), adapted for this GitOps repo
and this cluster's networking/TLS.

## Access

- **URL:** `https://openclaw.intersect.k8s.lfp.rocks` (LAN-only, via
  `internal-gateway-http`'s HTTPS listener; real Let's Encrypt cert).
- **Auth:** the `OPENCLAW_GATEWAY_TOKEN` (in the `openclaw-provider-keys` secret)
  **plus** per-device pairing (below). Read the token with:
  ```bash
  kubectl -n openclaw get secret openclaw-provider-keys \
    -o jsonpath='{.data.OPENCLAW_GATEWAY_TOKEN}' | base64 -d
  ```
- **Device pairing** (required for any remote/non-loopback browser): open the UI,
  paste the token in Settings; the request shows up as *pending* and must be
  approved server-side (or from an already-paired device with `operator.approvals`):
  ```bash
  POD=$(kubectl -n openclaw get pod -l sandbox=openclaw-template-sandbox \
    -o jsonpath='{.items[0].metadata.name}')
  kubectl -n openclaw exec $POD -c openclaw -- node /app/dist/index.js devices list
  kubectl -n openclaw exec $POD -c openclaw -- node /app/dist/index.js devices approve <request-id>
  ```
  Pairing is stored in `/workspace/.openclaw/devices` on the Ceph PVC, so it
  survives pod restarts.

## Resources

| File | What |
|------|------|
| `sandboxtemplate.yaml` | `SandboxTemplate`: `runtimeClassName: gvisor`, init container seeds config, `openclaw` gateway (`ghcr.io/openclaw/openclaw:2026.7.1`, pinned), hardened (non-root 1000, drop ALL, no privesc, seccomp; `readOnlyRootFilesystem: false` — OpenClaw writes to rootfs), 5Gi `ceph-block` workspace via `volumeClaimTemplates` at `/workspace/.openclaw`. **`networkPolicyManagement: Unmanaged`** (see Networking). Token + `ANTHROPIC_API_KEY` from the secret. |
| `sandboxwarmpool.yaml` | One pre-warmed sandbox. |
| `sandboxclaim.yaml` | Adopts a sandbox; stamps `sandbox.users.io/openclaw-claim` on its pod so the Service targets exactly the claimed sandbox. |
| `configmap.yaml` | `openclaw.json`: `controlUi.allowedOrigins` (incl. the https host), `trustedProxies` for the Cilium gateway, and a pinned **default model** `agents.defaults.model.primary` (needed since v2026.7.1's default flipped to OpenAI, which we have no key for). bind/port + `--allow-unconfigured` come from the CLI in the template. |
| `service.yaml` | ClusterIP `openclaw-gateway:18789`, selects the claim label. |
| `httproute.yaml` | `HTTPRoute` on `internal-gateway-http` → `openclaw-gateway:18789`, host `openclaw.${DOMAIN_COBRA_LANTERN}`. |
| `ciliumnetworkpolicy.yaml` | Replacement for the controller's default policy — see below. |
| `openclaw-secret.yaml` | SOPS-encrypted `openclaw-provider-keys` (`data`/base64): `OPENCLAW_GATEWAY_TOKEN` + `ANTHROPIC_API_KEY`. |

Namespace: `clusters/the-intersect/namespaces.yaml`. Flux Kustomization
`apps-openclaw`: `clusters/the-intersect/apps.yaml`. The internal HTTPS listener +
Cloudflare DNS-01 solver live in `infrastructure/configs/the-intersect/`
(`gateways.yaml`, `certmanager/`).

## Networking (the tricky part)

The agent-sandbox controller's **default** NetworkPolicy is unusable here: it
blocks `10.0.0.0/8` egress (kills cluster DNS → agent can't resolve Anthropic)
and only allows ingress from a `sandbox-router` that isn't deployed. So:

- `networkPolicyManagement: Unmanaged` on the SandboxTemplate stops the
  controller creating that policy.
- `ciliumnetworkpolicy.yaml` replaces it: **egress** = cluster DNS + `world`
  (internet), no internal LAN; **ingress** = only from the Cilium `ingress`
  entity (the gateway) + `host`, on 18789. A standard NetworkPolicy can't express
  the gateway identity — hence CiliumNetworkPolicy.

## Secret

`openclaw-provider-keys` uses `data`/base64 (not `stringData` — that form left a
stray `data: []` that broke Flux server-side apply). Edit it only with `sops`
(never `sed` — that corrupts the SOPS MAC):
```bash
sops apps/openclaw/the-intersect/openclaw-secret.yaml   # values are base64
```

## Gotchas

- **Editing the SandboxTemplate does NOT roll running pods.** Delete the
  `Sandbox` objects to regenerate (`kubectl -n openclaw delete sandbox --all`) —
  which also gives a fresh PVC, so you re-pair the device. Normal pod restarts
  keep the PVC/pairing.
- **ConfigMap changes need a pod recreate** (the init container copies config once
  at start) — mind the kubelet configmap-sync lag: recreate a bit after the
  configmap has settled, or the pod copies the stale version.
- **gVisor breaks `kubectl port-forward`** (the gateway listens in gVisor's
  netstack, not the host pod-netns loopback). Reach it via the HTTPS route only.
