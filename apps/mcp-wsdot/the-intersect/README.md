# mcp-wsdot

A Model Context Protocol (MCP) server for the Washington State Ferries (WSDOT)
API, hosted in-cluster over the **streamable HTTP** transport and consumed by
the [openclaw](../../openclaw/the-intersect/) agent.

Image: `ghcr.io/michaelpeterswa/mcp-wsdot:v1.1.0`
(source: <https://github.com/michaelpeterswa/mcp-wsdot>)

## Endpoints

| purpose      | address                                                  | auth              |
|--------------|----------------------------------------------------------|-------------------|
| MCP          | `http://mcp-wsdot.mcp-wsdot.svc.cluster.local:8080/mcp`   | `Bearer` token    |
| liveness     | `:8080/healthz`                                          | none              |
| readiness    | `:8080/readyz`                                           | none              |
| metrics      | `:8081/metrics`                                          | none (scraped)    |

The server is **cluster-internal only** (ClusterIP, no HTTPRoute). Access is
gated two ways: a `CiliumNetworkPolicy` that only admits the `openclaw`
namespace (plus node probes and the `grafana` metrics scraper), and a bearer
token every MCP request must carry.

## Secret

`secret.sops.yaml` is SOPS/age-encrypted and holds two keys:

- `WSDOT_API_KEY` — your WSDOT API access code (get one at
  <https://wsdot.wa.gov/traffic/api/>). **Committed as a placeholder — you must
  set the real value.**
- `MCP_AUTH_TOKEN` — the bearer token clients present. A random value was
  generated at creation.

Set the real WSDOT key without a full decrypt/edit cycle (run from the repo
root so `.sops.yaml` and the age key are found):

```sh
SOPS_AGE_KEY_FILE=age.agekey \
  sops set apps/mcp-wsdot/the-intersect/secret.sops.yaml \
  '["stringData"]["WSDOT_API_KEY"]' '"YOUR_REAL_KEY"'
```

Read the bearer token back (needed to wire openclaw, below):

```sh
SOPS_AGE_KEY_FILE=age.agekey \
  sops -d apps/mcp-wsdot/the-intersect/secret.sops.yaml \
  | grep MCP_AUTH_TOKEN
```

## Wiring openclaw to use this server

openclaw's egress policy has been extended (in
`apps/openclaw/the-intersect/ciliumnetworkpolicy.yaml`) so the sandbox may
reach this service on port 8080; DNS resolution was already permitted. That is
the only cluster-side change needed.

The server itself is registered **from within openclaw at runtime** (via the
Control UI), not through this repo. Use these connection details:

| field       | value                                                    |
|-------------|----------------------------------------------------------|
| transport   | `streamable-http`                                        |
| url         | `http://mcp-wsdot.mcp-wsdot.svc.cluster.local:8080/mcp`   |
| auth header | `Authorization: Bearer <MCP_AUTH_TOKEN>`                 |

Retrieve `MCP_AUTH_TOKEN` with the `sops -d` command under
[Secret](#secret) above.

## Configuration

Non-secret settings live in `configmap.yaml` (transport, ports, timeouts,
heartbeat). The full environment-variable reference is in the upstream repo's
README.
