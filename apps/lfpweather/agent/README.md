# lfpweather-agent

The conversational "ask the weather" agent for lfpweather.com. Two parts, both
from the `ghcr.io/michaelpeterswa/lfpweather-agent` image:

- **broker** (`broker.yaml`) — a long-lived Deployment. For each chat request it
  claims an Agent Sandbox for the browser session from the warm pool, reaches
  the agent at the sandbox pod IP, and reverse-proxies the streamed reply. Runs
  under a ServiceAccount with a Role scoped to SandboxClaims + Sandboxes.
- **agent** (`agent.yaml`) — a `SandboxTemplate` + `SandboxWarmPool`. Each
  sandbox runs the agent (a Claude tool-use loop over `lfpweather-mcp`) inside
  gVisor. One browser tab is one sandbox; the broker reaps it when idle.

```
frontend ──▶ lfpweather-broker ──▶ Agent Sandbox (agent) ──▶ lfpweather-mcp
 (route handler)   (Deployment)      (gVisor, warm pool)      └▶ Anthropic API (world)
```

## Isolation

`ciliumnetworkpolicy.yaml` (the controller's own policy is `Unmanaged`):

- **broker** — ingress only from the frontend; egress to DNS, the API server
  (SandboxClaims), and the agent pods.
- **agent sandbox** — ingress only from the broker; egress to DNS, `world` (the
  Anthropic API), and `lfpweather-mcp`. Nothing else in-cluster.

## Before this works (review checklist)

1. **Encrypt the secret.** `the-intersect/agent_secret.yaml` is a placeholder —
   set `ANTHROPIC_API_KEY` + `MCP_BEARER_TOKEN` and
   `sops --encrypt --in-place` it. Use a key from a **dedicated Anthropic
   workspace with a monthly spend cap**. `MCP_BEARER_TOKEN` is the same
   lfpweather-mcp bearer OpenClaw uses.
2. **Pin the image.** `broker.yaml` and the SandboxTemplate patch use
   `:v1.0.0` — set it to the tag semantic-release cut for the first
   `lfpweather-agent` release.
3. **Validate the sandbox control path.** `SANDBOX_ROUTER_URL` assumes an
   in-cluster `sandbox-router-svc` in `agent-sandbox-system`. Confirm the
   Service name/port, or unset it to use API-server port-forward (which also
   needs `pods/portforward: create` in the broker Role and is not granted here).
4. **Confirm RBAC verbs** against the SDK once running (SandboxClaims
   create/delete/get/list/watch; Sandboxes get).
5. **Warm-pool size** (`replicas: 2`) and the agent `RATE_LIMIT_*` / `MAX_TURNS`
   are conservative starting points — tune with real traffic.

Not yet wired (broker follow-up, tracked in the app repo): a **global daily
token budget** that reads each agent's `GET /usage` and degrades gracefully.
