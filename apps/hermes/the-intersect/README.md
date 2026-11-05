# hermes

[Hermes Agent](https://github.com/NousResearch/hermes-agent) in an agent-sandbox `Sandbox` under gVisor. It answers the owner's Telegram questions and sends a morning briefing, using the model on the DGX Spark. It runs beside `apps/openclaw`, and the design is in `docs/superpowers/specs/2026-11-04-hermes-agent-design.md`.

## Access

Telegram only, through its own bot, using long polling. Only the owner's ID in `TELEGRAM_ALLOWED_USERS` gets answers. There's no route or Service.

## Network

`ciliumnetworkpolicy.yaml` allows outbound access to the public internet and to these local destinations only: cluster DNS, the Spark on port 8000, and the `mcp-wsdot`, `home-assistant` and `lfpweather-mcp` MCP servers. `mcp-wsdot` has its own ingress allowlist that names this namespace.

Check it from a fresh pod carrying the `sandbox: hermes` label, so the result doesn't depend on a container the agent has used. Keep targets out of this public repo. Only a timeout counts as denied, because a refusal means the packet got through:

```bash
kubectl -n hermes exec -i <pod> -- \
  python3 -I -c "$(cat verify/verify_egress.py)" < targets.json
```

Home Assistant is limited by path as well as port, because its token isn't limited to MCP. Without a token, `/api/mcp` should return 405 for a GET, and `/api/states` should return 403 from the policy. A 401 on `/api/states` means the request reached Home Assistant, so the rule isn't working.

## Secrets

`secret.sops.yaml` holds `hermes-env`, with base64 values under `data`. To rotate a value, use `sops set`, then regenerate the Sandbox.

| Key | What it is | Shared? | Rotate at |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Hermes's bot | No | `@BotFather` |
| `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_HOME_CHANNEL`, `TELEGRAM_OWNER_ID` | Owner's ID | No | Not secret-sensitive, but kept out of git |
| `SPARK_API_KEY` | Spark server key | Yes, one key per server | `ansible` repo, then every caller |
| `WSDOT_MCP_TOKEN` | `mcp-wsdot` bearer | Yes, with OpenClaw | `apps/mcp-wsdot`, then both agents |
| `LFP_MCP_TOKEN` | `lfpweather-mcp` bearer | Yes, with OpenClaw | `apps/lfpweather/mcp`, then both agents |
| `HA_MCP_TOKEN` | Home Assistant long-lived token | Yes, with OpenClaw | Home Assistant profile, then both agents |

## Operations

- **Apply a change:** `kubectl -n hermes delete sandbox hermes`. The pod doesn't roll by itself. Flux recreates the Sandbox, and the PVC stays.
- **Logs:** `kubectl -n hermes logs hermes -c hermes`. Message text goes only to `gateway.log` on the volume, not to the pod's output.
- **`kubectl port-forward`** doesn't work under gVisor.
- **`kubectl exec`** can stay open after a command ends under gVisor, so use a timeout.

## What's different from OpenClaw

- **gVisor start:** needs the memory-backed `/run` and the s6 option in `sandbox.yaml`, and a Hermes-owned `home/` subdirectory on the volume. A restore has to keep that subdirectory.
- **SQLite:** uses rollback journaling rather than WAL.
- **Spark outages:** with recovery cycles off, a message fails after about 2 minutes, and you resend it.

## Accepted risks

- **Tokens:** an injected Hermes could likely read its tokens and send them out over the open internet, and only Hermes itself enforces the Telegram allowlist. The response is to rotate them, using the table above.
- **Home Assistant token:** the token carries its user's full REST API rights, not just the entities exposed through MCP. The HTTP rule enforces the MCP-only limit from this pod. Anyone holding a leaked token gets those full rights from wherever else Home Assistant is reachable.
- **Prompt injection:** the policy limits where Hermes connects, not what it does with access it holds. When an MCP failed in testing, it probed other ports by itself, and the policy contained that.
- **Memory:** Hermes writes its own memory and skills, so an injected instruction could persist. Backups are the way back.
