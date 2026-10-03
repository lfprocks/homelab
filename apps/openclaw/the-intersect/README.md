# openclaw

[OpenClaw](https://github.com/openclaw/openclaw) — a long-running personal AI
agent — running inside the [Agent Sandbox](https://github.com/kubernetes-sigs/agent-sandbox)
under **gVisor** on the-intersect. Based on the upstream
[`examples/openclaw-gvisor-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox/tree/main/examples/openclaw-gvisor-sandbox),
but deployed as a **single standalone `Sandbox`** (not
SandboxTemplate + WarmPool + Claim) so its state can live on an
**externally-owned PVC** that survives Sandbox regeneration. See *Durable state*.

## Access

- **URL:** `https://openclaw.${DOMAIN_COBRA_LANTERN}` (LAN-only, via
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
  Pairing is stored in `/workspace/.openclaw/devices` on the durable PVC, so it
  survives pod restarts **and** Sandbox regeneration.

## Resources

| File | What |
|------|------|
| `sandbox.yaml` | A single `Sandbox` (`agents.x-k8s.io/v1beta1`, name `openclaw`): `runtimeClassName: gvisor`, hardened (non-root 1000, drop ALL, no privesc, seccomp; `readOnlyRootFilesystem: false` — OpenClaw writes to rootfs). Two init containers — `init-config` (seeds base config from the ConfigMap) and `init-mcp` (materializes the MCP servers from the secret, see *MCP servers*). Mounts the external `openclaw-data` PVC at `/workspace/.openclaw`. `shutdownPolicy: Retain`. Secrets injected via `secretKeyRef`. |
| `pvc.yaml` | External, Flux-owned `openclaw-data` PVC (10Gi `ceph-block`, RWO). The durable home for agent state — **not** a Sandbox-owned `volumeClaimTemplate` (see *Durable state*). |
| `configmap.yaml` | `openclaw.json`: `controlUi.allowedOrigins` (incl. the https host), `trustedProxies` for the Cilium gateway, a pinned **default model** `agents.defaults.model.primary`, and the **Telegram channel** (`channels.telegram`, token-free — see *Telegram*). bind/port + `--allow-unconfigured` come from the CLI. No secrets here. |
| `service.yaml` | ClusterIP `openclaw-gateway:18789`, selects the pod label `sandbox: openclaw-template-sandbox`. |
| `httproute.yaml` | `HTTPRoute` on `internal-gateway-http` → `openclaw-gateway:18789`, host `openclaw.${DOMAIN_COBRA_LANTERN}`. |
| `ciliumnetworkpolicy.yaml` | Replacement for the controller's default policy — see *Networking*. Also allows egress to the two in-cluster MCP servers. |
| `openclaw-secret.yaml` | SOPS-encrypted `openclaw-provider-keys` (`data`/base64): `OPENCLAW_GATEWAY_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `WSDOT_MCP_TOKEN`, `HA_MCP_TOKEN`, `LFP_MCP_TOKEN`, and optionally `GITHUB_MCP_TOKEN` + `TELEGRAM_OWNER_ID` (see *Renovate approvals*). |
| `skills/` | Custom skills, packed into the `openclaw-skills` ConfigMap and copied to `/etc/openclaw/skills` by `init-config` (`skills.load.extraDirs`). |

Namespace: `clusters/the-intersect/namespaces.yaml`. Flux Kustomization
`apps-openclaw`: `clusters/the-intersect/apps.yaml`. The internal HTTPS listener +
Cloudflare DNS-01 solver live in `infrastructure/configs/the-intersect/`
(`gateways.yaml`, `certmanager/`).

## Durable state (why a standalone Sandbox)

Agent memory — sessions (`openclaw-agent.sqlite`), device pairing, credentials,
identity — lives under `/workspace/.openclaw`. Originally this was a
**`volumeClaimTemplates` PVC on the SandboxTemplate**, which the controller makes
**Sandbox-owned**. Applying an image bump means regenerating the Sandbox, and that
cascade-deletes the owned PVC — silently wiping the agent's memory.

The fix: own the PVC in Git (`pvc.yaml`) and mount it by `claimName`. Its
lifecycle is now decoupled from the Sandbox, so `kubectl -n openclaw delete
sandbox openclaw` (how you apply an image bump) preserves everything.

## MCP servers (secrets)

Two in-cluster MCP servers are wired in: `wsdot`
(`mcp-wsdot.mcp-wsdot.svc:8080/mcp`) and `homeassistant`
(`home-assistant.home-assistant.svc:8080/api/mcp`). Both are HTTP
(`streamable-http`) with a bearer token.

OpenClaw does **not** interpolate env vars in HTTP MCP headers (verified against
the binary + `docs/cli/mcp.md`; `--env` is stdio-only), so a `${VAR}` in the
config is **not** expanded — the token must be a literal. To keep the tokens in
the SOPS secret as the single source of truth, the `init-mcp` container
shell-expands the secret-backed env var into an idempotent upsert at boot:

```sh
node /app/dist/index.js mcp set wsdot "$(printf '{...,"headers":{"Authorization":"Bearer %s"}}' "$WSDOT_MCP_TOKEN")"
```

The *shell* substitutes `$WSDOT_MCP_TOKEN` before OpenClaw sees it; `mcp set` is
an idempotent write that does **not** connect to the target (safe at boot even if
the MCP server is down). URLs/transports stay readable in `sandbox.yaml`; only the
tokens come from the secret. Nothing plaintext is committed.

To rotate a token: `sops apps/openclaw/the-intersect/openclaw-secret.yaml`, update
the base64 value, then regenerate the Sandbox to re-materialize the config.

## Telegram

`channels.telegram` is enabled in the ConfigMap, but the bot token is **not** —
it comes from `TELEGRAM_BOT_TOKEN` (secret-backed env on the `openclaw`
container). OpenClaw resolves that env fallback for the **default account** only,
which is exactly what a `channels.telegram` block with no `accounts:` uses, so no
`botToken` literal is needed anywhere in Git.

`dmPolicy: "pairing"` (the default): the first DM to the bot creates a pending
pairing request that must be approved. Long polling to `api.telegram.org` is
covered by the existing `world` egress rule.

```bash
POD=$(kubectl -n openclaw get pod -l sandbox=openclaw-template-sandbox \
  -o jsonpath='{.items[0].metadata.name}')
kubectl -n openclaw exec $POD -c openclaw -- node /app/dist/index.js pairing list telegram
kubectl -n openclaw exec $POD -c openclaw -- node /app/dist/index.js pairing approve telegram <CODE>
```

Codes expire after 1 hour. Pairing state lives on the durable PVC. For a
one-owner bot you can tighten this to `dmPolicy: "allowlist"` with your numeric
Telegram user ID in `channels.telegram.allowFrom`.

## Renovate approvals

Renovate (`.github/renovate.json5`) opens a PR per version bump. Every morning
at 07:00 Pacific openclaw sends a Telegram digest of the open ones, grouped by
the `risk/*` label Renovate sets. Tap a PR's ✅ button (or reply `approve 131 132` /
`approve low`) and openclaw submits an approving review as you. Buttons need
`channels.telegram.capabilities.inlineButtons: "dm"` in the ConfigMap; taps reach
the agent as `callback_data: renovate:approve:<n>`. The
`.github/workflows/renovate-approved.yaml` workflow then enables auto-merge, and
GitHub merges once `validate` passes. The behaviour lives in
`skills/renovate-updates/SKILL.md`.

Pieces:

- **`github` MCP server**, set by `init-mcp` from `GITHUB_MCP_TOKEN`: a
  fine-grained PAT for `lfprocks/homelab` only, with **Pull requests:
  read/write** and **Contents, Issues, Commit statuses, Metadata: read**. No
  Contents write, so it can approve but can't push or merge, whatever a
  malicious release note talks it into.
- **`declare-cron` sidecar**: `cron add` needs the running gateway, so a native
  sidecar waits for it and upserts the `renovate-digest` job by declaration key.
  Re-running on every boot is a no-op. It skips the job, and just idles, while
  `GITHUB_MCP_TOKEN` or `TELEGRAM_OWNER_ID` is missing.
- **`TELEGRAM_OWNER_ID`**: your numeric Telegram user ID, where the digest is
  delivered.

Add both keys (they're optional, so the pod runs without them):

```bash
export SOPS_AGE_KEY_FILE=$PWD/age.agekey
f=apps/openclaw/the-intersect/openclaw-secret.yaml
sops set "$f" '["data"]["GITHUB_MCP_TOKEN"]' "\"$(printf %s "$PAT" | base64)\""
sops set "$f" '["data"]["TELEGRAM_OWNER_ID"]' "\"$(printf %s "$TG_ID" | base64)\""
```

Check after regenerating the Sandbox:

```bash
kubectl -n openclaw logs $POD -c declare-cron
kubectl -n openclaw exec $POD -c openclaw -- node /app/dist/index.js cron list
```

## OpenAI

`OPENAI_API_KEY` is injected for the non-agent OpenAI surfaces (images,
embeddings, speech). It does **not** change the agent model — `agents.defaults.
model.primary` stays pinned to `anthropic/claude-opus-4-8` in the ConfigMap.

## Networking (the tricky part)

The agent-sandbox controller's **default** NetworkPolicy is unusable here: it
blocks `10.0.0.0/8` egress (kills cluster DNS → agent can't resolve Anthropic)
and only allows ingress from a `sandbox-router` that isn't deployed. So
`ciliumnetworkpolicy.yaml` replaces it: **egress** = cluster DNS + `world`
(internet) + the two MCP services; **ingress** = only from the Cilium `ingress`
entity (the gateway) + `host`, on 18789. A standard NetworkPolicy can't express
the gateway identity — hence CiliumNetworkPolicy.

> Note: on the extensions `SandboxTemplate` this was paired with
> `networkPolicyManagement: Unmanaged` to suppress the controller's default. The
> core `Sandbox` has no such field; today the controller creates no default policy
> for a standalone Sandbox (`kubectl -n openclaw get networkpolicy` is empty). If
> that ever changes, set `spec.networkPolicy` on the Sandbox accordingly.

## Secret

`openclaw-provider-keys` uses `data`/base64 (not `stringData` — that form left a
stray `data: []` that broke Flux server-side apply). Edit it only with `sops`
(never `sed` — that corrupts the SOPS MAC):
```bash
sops apps/openclaw/the-intersect/openclaw-secret.yaml   # values are base64
```

## Gotchas

- **Editing `sandbox.yaml` / the ConfigMap does NOT roll the running pod.**
  Regenerate: `kubectl -n openclaw delete sandbox openclaw` (the controller
  recreates it from Git). The `openclaw-data` PVC is external, so sessions +
  pairing survive — unlike the old template path.
- **ConfigMap changes need a pod recreate** (the init container copies config once
  at start) — mind the kubelet configmap-sync lag: recreate a bit after the
  configmap has settled, or the pod copies the stale version.
- **gVisor breaks `kubectl port-forward`** (the gateway listens in gVisor's
  netstack, not the host pod-netns loopback). Reach it via the HTTPS route only.
