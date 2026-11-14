# hermes

[Hermes Agent](https://github.com/NousResearch/hermes-agent) in an agent-sandbox `Sandbox` under gVisor. It answers the owner's Telegram questions and sends a morning briefing, using the model on the DGX Spark. It replaced OpenClaw (decommissioned 2026-11-13); the design is in `docs/superpowers/specs/2026-11-04-hermes-agent-design.md`.

## Access

Telegram only, through its own bot, using long polling. Only the owner's ID in `TELEGRAM_ALLOWED_USERS` gets answers. There's no route or Service.

## Network

`ciliumnetworkpolicy.yaml` allows outbound access to the public internet and to these local destinations only: cluster DNS, the Spark on port 8000, and the `mcp-wsdot`, `home-assistant`, `lfpweather-mcp` and `mcp-brave-x402` MCP servers. `mcp-wsdot` and `mcp-brave-x402` have their own ingress allowlists that name this namespace.

Check it from a fresh pod carrying the `sandbox: hermes` label, so the result doesn't depend on a container the agent has used. Keep targets out of this public repo. Only a timeout counts as denied, because a refusal means the packet got through:

```bash
kubectl -n hermes exec -i <pod> -- \
  python3 -I -c "$(cat verify/verify_egress.py)" < targets.json
```

Home Assistant is limited by path as well as port, because its token isn't limited to MCP. Without a token, `/api/mcp` should return 405 for a GET, and `/api/states` should return 403 from the policy. A 401 on `/api/states` means the request reached Home Assistant, so the rule isn't working.

TCP checks can't prove the gateways are closed. Connections to a gateway's LoadBalancer IP succeed, and its Envoy then applies this policy to each request:

- **Internal gateway routes:** these return 403 "Access denied", even though the same request from an unrestricted pod reaches the service. Routes that only match certain paths or methods return the gateway's own 404 otherwise, so test them with a matching request.
- **Public routes:** the hostnames resolve to public addresses, so this pod reaches them the way any internet client does. Each app's own login is the only protection.

## Secrets

`secret.sops.yaml` holds `hermes-env`, with base64 values under `data`. To rotate a value, use `sops set`, then regenerate the Sandbox.

| Key | What it is | Shared? | Rotate at |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Hermes's bot | No | `@BotFather` |
| `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_HOME_CHANNEL`, `TELEGRAM_OWNER_ID` | Owner's ID | No | Not secret-sensitive, but kept out of git |
| `SPARK_API_KEY` | Spark server key | Yes, one key per server | `ansible` repo, then every caller |
| `WSDOT_MCP_TOKEN` | `mcp-wsdot` bearer | No | `apps/mcp-wsdot`, then here |
| `LFP_MCP_TOKEN` | `lfpweather-mcp` bearer | Yes, with `apps/lfpweather/agent` | `apps/lfpweather/mcp`, then both |
| `HA_MCP_TOKEN` | Home Assistant long-lived token | No | Home Assistant profile, then here |
| `BRAVE_MCP_TOKEN` | `mcp-brave-x402` bearer | No | `apps/mcp-brave-x402` (`MCP_AUTH_TOKEN`), then here |

## Operations

- **Apply a change:** `kubectl -n hermes delete sandbox hermes`. The pod doesn't roll by itself. Flux recreates the Sandbox, and the PVC stays.
- **Logs:** `kubectl -n hermes logs hermes -c hermes`. Message text goes only to `gateway.log` on the volume, not to the pod's output.
- **`kubectl port-forward`** doesn't work under gVisor.
- **`kubectl exec`** can stay open after a command ends under gVisor, so use a timeout.
- **Web search:** goes through the `brave` MCP server only. The built-in `web_search` (Nous's Perplexity gateway) is disabled, and `tools.tool_search` is off so MCP tools are visible to the model directly; with it on, Hermes defers every MCP tool behind a search bridge and the model tends to probe the server from the terminal instead.

## Morning briefing

The `declare-cron` sidecar creates `morning-briefing` from `briefing-prompt.md` when the job is missing. It runs at 06:45 Pacific and delivers to the Telegram home channel. If the pod is down at that time, it runs once on return, up to 2 hours late.

The briefing reads news feeds with nobody watching, so `platform_toolsets.cron` limits scheduled jobs to web tools and `lfpweather`. The built-in `web_search` is disabled everywhere (`agent.disabled_toolsets: [search]`), so in practice that means `web_extract` for fetching the feeds. They get no terminal, memory, skills, delegation or Home Assistant. A poisoned feed could still distort the briefing's text, so treat it as untrusted.

Hermes keeps jobs on the volume, so editing the prompt in git doesn't change the existing job. To apply an edit, remove the job with `hermes cron remove` and regenerate the Sandbox.

## What's different from OpenClaw

- **gVisor start:** needs the memory-backed `/run` and the s6 option in `sandbox.yaml`, and a Hermes-owned `home/` subdirectory on the volume. A restore has to keep that subdirectory.
- **SQLite:** uses rollback journaling rather than WAL.
- **Spark outages:** with recovery cycles off, a message fails after about 2 minutes, and you resend it.

## Accepted risks

- **Tokens:** an injected Hermes could likely read its tokens and send them out over the open internet, and only Hermes itself enforces the Telegram allowlist. The response is to rotate them, using the table above.
- **Home Assistant token:** the token carries its user's full REST API rights, not just the entities exposed through MCP. The HTTP rule enforces the MCP-only limit from this pod. Anyone holding a leaked token gets those full rights from wherever else Home Assistant is reachable.
- **Prompt injection:** the policy limits where Hermes connects, not what it does with access it holds. When an MCP failed in testing, it probed other ports by itself, and the policy contained that.
- **Injection drill:** in three runs from a `hermes chat` session, a test page told Hermes to switch on a device. It made no Home Assistant calls and flagged the text each time. That's evidence about one simple page on this model, not a guarantee, so repeat it after a model or version change.
- **Home Assistant exposure:** Hermes can control every entity exposed to Home Assistant's assistant settings. Review that list before exposing anything sensitive, such as locks or alarms.
- **Group chats:** access follows the person, so Hermes would answer the owner in any group it joined, where others could read the reply. Group joins are turned off in `@BotFather`. Keep them off.
- **Telegram allowlist:** tested by calling Hermes's authorization code with other user IDs, not with a real message from a second account.
- **Briefing accuracy:** the model has invented a unit and made spelling slips in briefing text. Check anything that matters against the source.
- **Memory:** Hermes writes its own memory and skills, so an injected instruction could persist. Backups are the way back.
- **Paid search:** an injected Hermes could burn the Brave search budget, up to the daily cap that `mcp-brave-x402` enforces (default $1/day), and alerts fire at 80% and at the cap. The wallet key stays in that pod, so Hermes can't reach the wallet itself. Scheduled jobs don't get the tool.
