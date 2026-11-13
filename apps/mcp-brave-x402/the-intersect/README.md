# mcp-brave-x402

A Model Context Protocol (MCP) server giving agents Brave web, news and
LLM-context search with no Brave API key. Each call is paid per request in USDC
on Base via the [x402](https://x402.org) protocol, from a hot wallet the server
holds. Hosted in-cluster over the **streamable HTTP** transport and consumed by
the `hermes` agent.

Image: `git.intersect.computer/michaelpeterswa/mcp-brave-x402:1.0.0`
(source: <https://git.intersect.computer/michaelpeterswa/mcp-brave-x402>).
The registry pull secret is injected by Kyverno; the Deployment sets no
`imagePullSecrets`.

## Cost

Every search costs US$0.005 (5000 USDC base units), whatever the tool. Spend is
capped per day (default US$1.00, about 200 calls) and the cap resets at
midnight Pacific. The wallet is a dedicated hot wallet funded with a small
float only; the server refuses to sign anything outside its offer allowlist
(Brave's live offer is the default, so no allowlist values are set here).

## Endpoints

| purpose   | address                                                           | auth           |
|-----------|-------------------------------------------------------------------|----------------|
| MCP       | `http://mcp-brave-x402.mcp-brave-x402.svc.cluster.local:8080/mcp` | `Bearer` token |
| liveness  | `:8080/healthz`                                                   | none           |
| readiness | `:8080/readyz`                                                    | none           |
| metrics   | `:8081/metrics`                                                   | none (scraped) |

Cluster-internal only (ClusterIP, no HTTPRoute). The `CiliumNetworkPolicy`
admits the `hermes` namespace, node probes and the `grafana` scraper, and
allows egress only to cluster DNS and the public internet on 443 (private
ranges excepted).

## Secret

`secret.sops.yaml` (Secret `mcp-brave-x402-secret`, SOPS/age) holds two keys.
It is **not yet committed**: it is created after the wallet exists, and
`kustomization.yaml` lists it commented out until then.

| key                  | what                                   | rotation                                                                 |
|----------------------|----------------------------------------|--------------------------------------------------------------------------|
| `WALLET_PRIVATE_KEY` | hot wallet that pays for searches      | make a new wallet, move the float to it, update the secret               |
| `MCP_AUTH_TOKEN`     | bearer token, shared with Hermes       | `openssl rand -hex 32`; update here and in the Hermes MCP config together |

Never paste the private key into chat, the ConfigMap or a plain file. Set it
without a full decrypt/edit cycle (from the repo root):

```sh
SOPS_AGE_KEY_FILE=age.agekey \
  sops set apps/mcp-brave-x402/the-intersect/secret.sops.yaml \
  '["stringData"]["WALLET_PRIVATE_KEY"]' '"<key from 1Password>"'
```

## Changing the daily cap

Edit `DAILY_BUDGET_USDC` in `configmap.yaml` (decimal USDC, up to 6 places) and
merge; reloader restarts the pod. The per-call maximum and the offer allowlist
are server defaults; see the upstream README before changing them.

## Alerts

Defined in group `brave-x402` of
`infrastructure/configs/the-intersect/grafana/lgtm-distributed/values.yaml`.

| alert                       | fires when                                              | meaning / what to check |
|-----------------------------|---------------------------------------------------------|-------------------------|
| `BraveX402Spend80` (warn)   | spend today / daily cap >= 0.8                          | heavy use or a loop; check the logs, raise the cap if legitimate |
| `BraveX402CapHit` (warn)    | a call was refused for the cap in the last 15m          | searches fail until Pacific midnight; raise the cap if legitimate |
| `BraveX402LowBalance` (warn)| wallet balance < 2 USDC for 15m (or no data)            | top up the wallet on Base |
| `BraveX402BalanceStale` (warn) | balance poll has not succeeded for 30m (or no data)  | low-balance alert is blind; check logs for RPC errors |
| `BraveX402OfferRefused` (critical) | an offer was refused by the allowlist in the last 15m | Brave changed its offer or the proxy is being tampered with; nothing was signed; verify the new offer before widening `ALLOWED_*` |
