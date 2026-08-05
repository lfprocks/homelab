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

- [x] **Secret encrypted.** `the-intersect/agent_secret.yaml` holds the SOPS-
  encrypted `ANTHROPIC_API_KEY` (dedicated workspace, $20/mo cap) and
  `MCP_BEARER_TOKEN` (the lfpweather-mcp bearer OpenClaw uses).
- [x] **Image pinned** to `:v1.1.2` (broker Deployment + SandboxTemplate patch).
- [x] **Daily budget set** — `DAILY_TOKEN_BUDGET: "100000"` on the broker,
  sized to spread the workspace's $20/mo cap and degrade gracefully below it.
- [x] **Sandbox control path validated.** This install runs no router and the
  agent pods carry no runtime sidecar, so `SANDBOX_ROUTER_URL` is set only to
  select the SDK's direct mode. The broker reads the pod IP from
  `Sandbox.status.podIPs` and proxies HTTP to it — the router/runtime and
  `pods/portforward` are never used.
- [x] **RBAC confirmed** against the live CRDs (`extensions.agents.x-k8s.io`
  SandboxClaims create/delete/get/list/watch; `agents.x-k8s.io` Sandboxes
  get/list/watch).
- [ ] **Tune** warm-pool size (`replicas: 2`) and the agent `RATE_LIMIT_*` /
  `MAX_TURNS` with real traffic; watch the broker's `GET /usage`.
