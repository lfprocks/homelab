# cert-manager (the-intersect)

ClusterIssuers + the DNS-01 provider credentials.

## ClusterIssuers (`cluster-issuer.yaml`)

- `letsencrypt-prod` / `letsencrypt-staging` — ACME with **two DNS-01 solvers**,
  matched by zone:
  - **Cloudflare** for `lfp.rocks` (`selector.dnsZones: [lfp.rocks]`) — token in
    the `cloudflare-api-token` secret.
  - **Route53** (default, no selector) for everything else
    (`intersect.computer`, `lfpweather.com`, `halcyonresearch.dev`, …) — creds in
    `certmanager-route53-clusterissuer-secret`.
- `selfsigned-cluster-issuer` — self-signed, for anything that can't use ACME.

## Secrets (SOPS-encrypted, `data`/base64)

- `aws-secret.yaml` → `certmanager-route53-clusterissuer-secret` (Route53).
- `cloudflare-secret.yaml` → `cloudflare-api-token` (key `api-token`). The token
  needs **Zone → DNS → Edit** on the `lfp.rocks` zone. Edit with `sops` only.

## Adding a new internal HTTPS host

`internal-gateway-http` (`../gateways.yaml`) has a `default-https` listener for
`*.${DOMAIN_COBRA_LANTERN}` (= `*.intersect.k8s.lfp.rocks`) with the
`cert-manager.io/cluster-issuer: letsencrypt-prod` gateway-shim annotation — so
the wildcard cert (`cobra-lantern-tls`) is issued automatically via Cloudflare.
Any internal app under that domain just needs an `HTTPRoute` on
`internal-gateway-http` (see `apps/openclaw`); no per-app cert work.

> The internal domain is Cloudflare-hosted; the public ones are on Route53. If a
> DNS-01 challenge fails with "zone not found in Route 53", the zone is on the
> wrong provider — check the solver `dnsZones` selectors.
