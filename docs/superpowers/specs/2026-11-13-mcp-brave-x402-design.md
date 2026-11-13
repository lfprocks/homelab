# mcp-brave-x402 design

**Date:** 2026-11-13
**Status:** draft, awaiting owner approval

## Intent

Hermes has no web search. The design for Hermes (`2026-11-04-hermes-agent-design.md`) left paid search as the owner's decision, and the owner has now chosen it. The search comes from Brave's pay-per-request proxy at `https://search.agent.s.brave.app` (see its `/llms.txt`). Each request is paid with an x402 micropayment of $0.005 in USDC on Base. There's no account and no API key: a signed payment is the credential.

Hermes is prompt-injectable, and its README already accepts that an injected Hermes could send its tokens out. A leaked token can be rotated, but a leaked wallet key loses money. So the wallet key lives in a separate MCP server that Hermes calls with an ordinary bearer token. That server only searches Brave, enforces a daily spending cap, and reports its spend and the wallet balance as metrics you can alert on.

**Uses:** live lookups while chatting with Hermes on Telegram, research, and other legitimate tasks.

## Goals

- A Go MCP server, `mcp-brave-x402`, in a new Forgejo repo at `git.intersect.computer/michaelpeterswa/mcp-brave-x402`. Its local checkout is `/Users/michaelpeters/go/src/git.intersect.computer/michaelpeterswa/mcp-brave-x402`.
- It offers Brave search tools to Hermes and pays for each call from a wallet whose key only this server holds.
- **Daily spending cap:** $1/day by default, configurable. Past it, the server refuses with a clear tool error, and an alert fires.
- **Metrics:** calls, spend, refusals and the wallet's USDC balance, all as OpenTelemetry metrics, with Grafana alerts on them.
- Deployed on the-intersect and wired into Hermes, with a CiliumNetworkPolicy on each side.

## Non-goals

- Callers other than Hermes. OpenClaw and local Claude Code are out of scope for now: there's one bearer token, and the server stays cluster-internal with no route.
- Automating wallet management. The owner creates a **dedicated hot wallet used only for search**, keeps its key in 1Password, and tops it up by hand from their main wallet with a small float, about $5–10 of USDC. The main wallet's key never touches the cluster. A leaked key costs at most the float.
- Brave endpoints the proxy doesn't sell (Answers, Autosuggest, Spellcheck, Summarizer).
- A generic x402 fetch tool. The server must never pay any URL other than the Brave proxy.
- Search for Hermes's scheduled jobs. The morning briefing reads untrusted feeds with nobody watching, so it doesn't get a tool that spends money.

## Anti-goals (a failure even if it works)

- The wallet key reaches Hermes, logs, metrics, git in plain text, or an error message.
- The server signs a payment for anything other than the advertised offer: USDC on Base (`eip155:8453`), asset `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, no more than 5000 base units, paid to the expected recipient. A changed or tampered 402 challenge must be refused, not paid.
- Spend can go past the cap, for example through concurrent calls racing the check.
- The wallet drains, or the RPC dies, without an alert.

## Constraints

- **Repo format:** match the owner's existing Go MCP servers, with `github.com/michaelpeterswa/lfpweather-mcp` as the template and `mcp-wsdot` as a second reference:
  - `cmd/mcp-brave-x402/main.go`, plus `internal/config`, `internal/logging`, `internal/mcpserver` (with `http.go` and its test) and `internal/handlers`, and a new `internal/brave` package for the paying client.
  - Libraries: `mark3labs/mcp-go` over streamable HTTP on :8080, metrics on :8081 through `alpineworks.io/ootel`, and `caarlos0/env/v11` for config.
  - The same `.githooks/`, `commitlint.config.js`, `.releaserc`, `.dockerignore`, `.gitignore`, `docker-compose.yml` with `docker/grafana`, Makefile, MIT `LICENSE`, and the README style.
  - `/healthz` and `/readyz`, as the homelab deployment probes expect.
- **Dockerfile:** the same three-stage `go.dockerfile` as `lfpweather-mcp`:
  - `golang:1-bookworm` cross-compiling via `TARGETARCH`, then `debian:bookworm` with ca-certificates/curl/tzdata.
  - `PKG_VER_PATH` points at `git.intersect.computer/michaelpeterswa/mcp-brave-x402/internal/config.AppVersion`.
  - OCI labels point at the Forgejo repo.
  - EXPOSE 8080/8081.
- **Go module path:** `git.intersect.computer/michaelpeterswa/mcp-brave-x402`.
- **x402 library:** the x402 Foundation's Go module, `github.com/x402-foundation/x402/go`, which moved from `coinbase/x402`. Use its "exact" EVM client with a local private-key signer. Pin a real tag after checking it, and use x402 V2 headers: `Payment-Required`, `Payment-Signature`, `Payment-Response`. The proxy doesn't recognize V1 `X-Payment`.
- **CI:** this repo lives on Forgejo, so the workflows go in `.forgejo/workflows/`.
  - Keep the same jobs as the GitHub template: commitlint, golangci-lint, yamllint, hadolint and `go test`.
  - Build the image the way `git.intersect.computer/michaelpeterswa/gambling/.forgejo/workflows/docker.yml` does: remote BuildKit at `tcp://buildkitd.forgejo-runners.svc.cluster.local:1234`, since runners have no Docker daemon. Push to `git.intersect.computer/michaelpeterswa/mcp-brave-x402` using `vars.REGISTRY_USER` / `secrets.REGISTRY_TOKEN`.
  - Pin every third-party action by commit SHA.
  - Build `linux/amd64` only. *(Builder's call: the cluster nodes are amd64, and the rootless BuildKit may not have the emulation needed for an arm64 `apt-get` stage. The GitHub template's multi-arch came from QEMU on GitHub runners.)*
- **Homelab:** the owner's rules apply:
  - New CNPs never use `toEntities: world`. They use `0.0.0.0/0` minus the private ranges.
  - Secrets are SOPS-encrypted.
  - Run as non-root uid 65532 with a read-only root filesystem and a `/tmp` emptyDir, as `mcp-wsdot` does.
  - No environment details (IPs, wallet addresses, hostnames beyond the public proxy) go in public repos. The homelab repo is public.
- **Registry (owner's decision):** the repo and image stay under the `michaelpeterswa` personal namespace, and the **container package is public**. That lets the Kyverno-injected `forgejo-registry-pull` secret (`cluster-pull`) pull it like any other image. The code holds no secrets, and the key and token come from the cluster Secret. The `ci-publisher` bot can't push to personal namespaces, so CI pushes with the owner's own `write:package` token, set as `REGISTRY_USER` (variable) and `REGISTRY_TOKEN` (secret). If those aren't already set at user level the way `gambling` uses them, setting them on the repo is a manual prerequisite.

## How it works (decided)

### Tools

Three tools, mapped to proxy paths. The proxy's `/openapi.json` (checked 2026-11-13) prices every sold path at the same 5000 units, `llm/context` included, so one per-call maximum covers all three.

| Tool | Proxy path | Why |
|---|---|---|
| `web_search` | `/res/v1/web/search` | general lookups |
| `news_search` | `/res/v1/news/search` | current events |
| `llm_context` | `/res/v1/llm/context` | page text selected for prompts, for research |

The tools take a `query` plus a small, typed set of optional Brave parameters, such as count, freshness, country and safesearch. Each tool's description says it costs money, so the model doesn't call it idly. Images, videos and local search are left out for now.

Results are trimmed before they reach Hermes, to save context: title, URL, snippet and age for search results, and source URLs plus selected text for `llm_context`. Brave's full JSON isn't passed through.

### Payment flow

1. GET the proxy path with no payment header.
2. On a 402, decode the `Payment-Required` challenge.
3. Check that the offer matches the configured allowlist: network, asset, recipient, and an amount no higher than the configured maximum. On any mismatch, refuse without signing and return a tool error.
4. Reserve the cost against the daily budget. If the reservation fails, refuse.
5. Sign, and retry the identical request with `Payment-Signature`.
6. If the response is 2xx with a `Payment-Response` receipt, record the spend.
   - A failed search isn't charged by the proxy, so release the reservation.
   - On a 502 where settlement may have happened, count it as spent. Spending a little more than reported is better than hiding real spend.

### Spending cap

- Configured in USDC, defaulting to `1.00`, which is 200 calls.
- Resets at midnight `America/Los_Angeles`.
- Kept in memory, behind a mutex. A reservation happens before signing, so concurrent calls can't go past the cap.
- A restart resets the counter. That's acceptable because Hermes can't restart the pod, and a restart loop trips the existing crashloop alert.
- The refusal tells the model the daily search budget is used up and resets at midnight Pacific.

### Metrics (OpenTelemetry via ootel, scraped on :8081)

- **Calls:** a counter by tool and outcome (`paid`, `refused_cap`, `refused_offer`, `upstream_error`, `payment_error`).
- **Spend:** a counter in USDC, plus a gauge of today's spend and the configured cap.
- **Wallet balance:** a gauge in USDC, read on a timer (every 5 minutes by default) with an ERC-20 `balanceOf` call against a configurable Base RPC URL. A second gauge holds the time of the last successful read.

### Alerts (Grafana, Pushover route)

- Daily spend at 80% of the cap, and at 100% (the cap was hit).
- Wallet balance below a threshold, $2 by default.
- No balance read in over 30 minutes, so a dead RPC can't hide a draining wallet.
- Any `refused_offer`, because it means the proxy's terms changed or something tampered with the challenge.

### Config (env vars)

- **Secrets:** wallet private key and the Hermes bearer token.
- **Spending:** daily cap in USDC, and the maximum amount allowed per call.
- **Offer allowlist:** expected network, asset and recipient. Take the recipient from a live 402 during build and pin it.
- **Endpoints:** proxy base URL and Base RPC URL.
- **Balance check:** poll interval.
- **Standard:** ports and log level, as in the template.

The server refuses to start if the key or the bearer token is missing.

### Security

- The bearer token is required on every MCP request, as for `mcp-wsdot` and `lfpweather-mcp`.
- The key is parsed once at startup and never logged. Error messages never include signed payloads.
- Requests only go to the configured proxy base URL and its three paths. The tools can't steer the host or path.

### Homelab deployment (`apps/mcp-brave-x402/the-intersect`)

Modelled on `apps/mcp-wsdot`:
- Deployment, Service, ConfigMap, `secret.sops.yaml`, kustomization, README, and a Flux Kustomization in `clusters/the-intersect/apps.yaml`, plus the namespace.
- **CNP ingress:** from the `hermes` namespace on 8080, the host for probes, and `grafana` on 8081.
- **CNP egress:** DNS, and 443 to the public internet minus private ranges. That covers the Brave proxy and the Base RPC.
- Alert rules go wherever the existing app alerts live.

### Hermes wiring (`apps/hermes/the-intersect`)

- `configmap.yaml`: add a `brave` entry under `mcp_servers` with `Authorization: Bearer ${BRAVE_MCP_TOKEN}`. Leave `platform_toolsets.cron` unchanged, so scheduled jobs don't get it.
- `secret.sops.yaml`: add `BRAVE_MCP_TOKEN`.
- `ciliumnetworkpolicy.yaml`: add egress to the new pod on 8080.
- README: add rows to the secrets table and the accepted-risks list.
- Apply by deleting the Sandbox, per the Hermes README.

## Left to the builder

- Exact Brave query parameters each tool exposes, and the trimmed result shapes.
- Whether `internal/brave` wraps the x402 client's HTTP transport or drives the 402 handshake itself. The constraint is that the offer is checked before signing.
- Metric names, following the existing apps' conventions.
- A default public Base RPC endpoint.
- Semantic-release on Forgejo: use a Forgejo-compatible plugin if one works with the runner. Otherwise, tag by hand and leave a note in the README.
- Tests. At minimum:
  - offer validation, including wrong asset, recipient, network or amount
  - cap reservation under concurrency
  - midnight Pacific reset
  - result trimming
  - the auth middleware
  - a fake 402 server for the handshake

## Order

1. The owner creates the empty repo in Forgejo (done 2026-11-13). The owner sets `REGISTRY_USER`/`REGISTRY_TOKEN` if needed, and creates and funds the hot wallet before step 6.
2. Build the server and the repo scaffolding, with CI green on a PR.
3. Make the published package public, and confirm a node can pull it.
4. Homelab PR: the new app plus alerts.
5. Homelab PR: Hermes wiring.
6. Fund-check and verify end to end:
   - A real paid search from Hermes on Telegram.
   - The receipt appears on-chain.
   - Metrics and alerts show it.
   - Setting the cap to $0.005 refuses the second call.
