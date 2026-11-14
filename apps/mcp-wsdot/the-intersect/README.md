# mcp-wsdot

A Model Context Protocol (MCP) server for the Washington State Ferries (WSDOT)
API, hosted in-cluster over the **streamable HTTP** transport and consumed by
the [Hermes](../../hermes/the-intersect/) agent.

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
gated two ways: a `CiliumNetworkPolicy` that only admits the `hermes`
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

Read the bearer token back (Hermes holds it as `WSDOT_MCP_TOKEN`):

```sh
SOPS_AGE_KEY_FILE=age.agekey \
  sops -d apps/mcp-wsdot/the-intersect/secret.sops.yaml \
  | grep MCP_AUTH_TOKEN
```

## Wiring a client

Hermes is the only client: `apps/hermes/the-intersect` registers this server in
its `config.yaml` (`mcp_servers.wsdot`, bearer `${WSDOT_MCP_TOKEN}`) and its
egress policy allows this service on port 8080. A new client needs both, plus
an ingress rule for its namespace in `ciliumnetworkpolicy.yaml` here.

## Configuration

Non-secret settings live in `configmap.yaml` (transport, ports, timeouts,
heartbeat). The full environment-variable reference is in the upstream repo's
README.
