# openclaw

[OpenClaw](https://github.com/openclaw/openclaw) — a long-running personal AI
agent gateway. Deployed here as a hardened, **gVisor-sandboxed** singleton on
the-intersect.

## Shape

- `deployment.yaml` — `openclaw` gateway (init copies config into the home PVC),
  image `ghcr.io/openclaw/openclaw:slim`, port `18789`, hardened
  (non-root/1000, read-only rootfs, drop ALL caps, seccomp RuntimeDefault) and
  pinned to **`runtimeClassName: gvisor`** so the agent runs in a userspace-kernel
  sandbox on the workers.
- `pvc.yaml` — 10Gi Ceph RBD (`ceph-block`) home volume (agent state/workspace).
- `configmap.yaml` — `openclaw.json` (gateway: token auth, Control UI on) + `AGENTS.md`.
- `service.yaml` — ClusterIP `openclaw:18789`.
- `openclaw-secret.yaml` — SOPS-encrypted `openclaw-secrets`
  (`OPENCLAW_GATEWAY_TOKEN` generated; `ANTHROPIC_API_KEY` is a **placeholder**).

Wired into Flux via `clusters/the-intersect/apps.yaml` (`apps-openclaw`), namespace
declared in `clusters/the-intersect/namespaces.yaml`.

## Before it works: set the API key

The committed secret ships a placeholder API key. Inject your real one (reuse the
key already in `transcribe-secret`, or provision a new one) and re-commit:

```bash
sops set apps/openclaw/the-intersect/openclaw-secret.yaml \
  '["stringData"]["ANTHROPIC_API_KEY"]' '"sk-ant-..."'
```

## Access (after it's Running)

The gateway binds loopback by default — reach the Control UI via port-forward:

```bash
kubectl -n openclaw port-forward svc/openclaw 18789:18789
# token: kubectl -n openclaw get secret openclaw-secrets \
#   -o jsonpath='{.data.OPENCLAW_GATEWAY_TOKEN}' | base64 -d
open http://localhost:18789
```

## Notes / follow-ups

- **Model:** defaults to Anthropic/Claude via `ANTHROPIC_API_KEY`. Other providers
  (OpenAI/Gemini/OpenRouter) are supported upstream — add the key + env if wanted.
- **gVisor vs Agent Sandbox:** this runs as a plain `Deployment` + `runtimeClassName:
  gvisor` (reliable, upstream-aligned, isolation achieved now). Migrating it to an
  `agents.x-k8s.io/Sandbox` (the controller installed via the agent-sandbox PR) —
  for pause/resume, warm pools, and stable identity — is a sensible next iteration.
- **Image tag** is the rolling `:slim`; consider pinning + Renovate.
- **PSS:** namespace enforces `baseline` (audits/warns `restricted`) — stricter than
  the repo's `privileged` default, which the hardened pod satisfies.
- **External access:** no HTTPRoute yet (loopback + port-forward). Add one via the
  shared gateway if you want it exposed (mind auth/TLS for an agent UI).
