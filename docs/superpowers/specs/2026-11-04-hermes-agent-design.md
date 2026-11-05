# Hermes Agent on the DGX Spark model

Status: design, awaiting approval. Date: 2026-11-04.

This repo is public. Nothing here or in the resulting PRs may contain a real hostname under the codename scheme, a token, or a Telegram ID.

## Intent

Run [Hermes Agent](https://github.com/NousResearch/hermes-agent) as a personal assistant on the model already serving from the DGX Spark, using the same deployment pattern as OpenClaw, so OpenClaw can eventually be phased out.

Day to day, OpenClaw is used for two things: the morning briefing and answering questions over Telegram. Hermes has to do those two things well. It doesn't need to reproduce the rest of OpenClaw.

## Goals

- Hermes answers Telegram questions from the owner, using the Spark model.
- Hermes sends a daily morning briefing to Telegram.
- It runs in an agent-sandbox `Sandbox` under gVisor, deployed through Flux and SOPS like `apps/openclaw/the-intersect`.
- It can reach the public internet, and only named local resources.
- State survives Sandbox regeneration and is backed up.

## Non-goals

- Turning OpenClaw off. This project ends when the switch decision can be made. Renovate approvals keep OpenClaw running regardless.
- Moving the Renovate digest or approvals, or giving Hermes a GitHub token.
- A dashboard, web UI, or inbound route.
- Group chats, other messaging platforms, or other users.
- Local model hosting inside Hermes, Nous Portal, or the Tool Gateway.

## Anti-goals

These would make this a failure even if it technically works:

- Hermes can reach anything local beyond the allowlist below, such as olympic, the router, iDRACs, the Kubernetes API, Ceph, or the Spark's SSH port.
- Anyone other than the owner can talk to it.
- The Hermes rollout disturbs OpenClaw, including its bot, its state, or its Renovate flow.
- A secret or real hostname lands in this public repo.
- Local network containment depends on anything inside the agent process. Hermes's own security policy says only the OS-level boundary counts. The two accepted exceptions are under "Decided by the owner".

## Decided by the owner

**Network, outbound:** the public internet is allowed, and local access is limited to:

| Destination | Port | Purpose |
|---|---|---|
| Cluster DNS (`kube-system`, `k8s-app: kube-dns`) | 53 UDP/TCP | Name resolution |
| Public internet, excepting private ranges | any | Telegram, web access |
| Spark, `10.0.2.100/32` | 8000 TCP | The model |
| `mcp-wsdot` pods | 8080 | MCP |
| `home-assistant` pods | 8080, 8123 | MCP |
| `lfpweather-mcp` pods | 80, 8080 | MCP |

The private-range exception is the same as OpenClaw's: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `100.64.0.0/10`, `169.254.0.0/16`. The in-cluster rules copy the selectors in `apps/openclaw/the-intersect/ciliumnetworkpolicy.yaml`.

**Switch bar:** "a few good briefings and solid Telegram answers", judged by the owner. There's no numeric threshold.

**Tokens and the Telegram allowlist:** accepted, the same as OpenClaw. Secrets reach the pod as env vars, and Hermes itself enforces `TELEGRAM_ALLOWED_USERS`. A prompt-injected Hermes could likely read its tokens and send them out over the open internet. There's no relay or credential proxy. Record this in the app README, and use separate, revocable tokens wherever services allow it.

**Homelab services on public hostnames:** treated as public internet, not local. Several homelab services have routes on the external gateway, and the policy doesn't try to block them. No credentials for them are given to Hermes, so their own logins still protect them. Test which are reachable from the sandbox, show the owner, and record the result as an accepted limit in the README.

**Spark contention:** site summaries take priority, and a slightly slower Hermes is acceptable. Measure this in the spike. If summaries slow down or time out, limit Hermes's concurrency and context first. Retuning the Spark server is a separate `ansible` change that comes back to the owner.

## Design calls made for the owner

The owner can overrule these while reviewing this document.

- **No inbound access.** No HTTPRoute or Service. The network policy allows ingress only from `host`, on the health port if one is used. Telegram uses long polling, which is outbound.
- **Own namespace:** `hermes`, with the same labels as `openclaw`.
- **Own Telegram bot.** One token can't be polled by two agents. The owner creates the bot and supplies its token. Set `TELEGRAM_ALLOWED_USERS` to the owner's ID only.
- **Own credentials where possible.** New tokens for Home Assistant, WSDOT and lfpweather MCP if those services can issue more than one, so either agent can be revoked alone. Otherwise reuse, and record which were shared. The Spark server has one API key, so that's shared with the forecast API.
- **Spark by IP:** `http://10.0.2.100:8000/v1`, with the model name `Qwen3.8-Flash-Next`. The policy pins that IP anyway, and this avoids depending on the name resolving (it failed once on 2026-11-04). The address is private and already implied by the policy, so the URL can sit in config rather than the secret. As with the forecast API, this traffic is plain HTTP on the LAN.
- **Image:** `nousresearch/hermes-agent:v0.22.7@sha256:e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c`, not the `desktop` variant. Renovate may propose bumps, without automerge.
- **Git holds intent, the PVC holds state.** Config, persona seed and the briefing definition come from git. Memory, sessions and learned skills live on the PVC.
- **Selective import.** Preview with `hermes import-agent openclaw --dry-run`, then bring over persona and memory only, never secrets. OpenClaw's workspace files are small (`SOUL.md`, `USER.md`, `MEMORY.md`, `IDENTITY.md`). Don't connect the two agents' PVCs. Copy by a reviewed manual step, and the import must leave OpenClaw's state untouched.
- **Home Assistant access matches OpenClaw's**, as the owner chose. Before go-live, list what HA exposes through its MCP server and show the owner, because that's the limit on what a tricked agent could change.
- **Terminal backend stays local to the sandbox.** The sandbox is the boundary, so a nested container isn't needed.
- **No external messages or token-bearing tools beyond the list above.** Don't add API keys for optional services like image generation or paid search.
- **Web search is wanted but doesn't block the build.** OpenClaw has keyless DuckDuckGo search. Test whether Hermes can search without a paid key. If it can't, the build can still pass, but tell the owner before the comparison so answers aren't judged on a capability Hermes wasn't given. Adding a paid key or a self-hosted search service is the owner's decision.

## Deployment shape

New directory `apps/hermes/the-intersect/`, mirroring OpenClaw:

- `sandbox.yaml`: standalone `Sandbox` (`agents.x-k8s.io/v1beta1`), `runtimeClassName: gvisor`, `automountServiceAccountToken: false`, non-root, all capabilities dropped, `allowPrivilegeEscalation: false`, `RuntimeDefault` seccomp. Labels `k8s.lfp.rocks/service` and `version`, plus a `sandbox:` label for the policy's selector.
- `pvc.yaml`: Flux-owned `hermes-data`, 10Gi `ceph-block`, mounted at `/opt/data`. Never a `volumeClaimTemplate`, because those are deleted with the Sandbox.
- `configmap.yaml`: Hermes `config.yaml` and seed files.
- `ciliumnetworkpolicy.yaml`: the table above.
- `secret.sops.yaml`: Telegram token, owner ID, Spark key, MCP tokens. Use `data`/base64, because `stringData` broke Flux server-side apply for OpenClaw.
- `kustomization.yaml`, plus the namespace and `apps-hermes` Flux Kustomization.
- `README.md`: access, operations, and what differs from OpenClaw.

Backups: add `hermes-hermes-data` ReplicationSource and restic secret under `infrastructure/configs/the-intersect/volsync-backups/`, copying OpenClaw's retention. Use a different schedule from `50 2 * * *`, and set the mover's UID to match the pod.

### Known traps from OpenClaw

- Editing `sandbox.yaml` or the ConfigMap doesn't roll the pod. Apply with `kubectl -n hermes delete sandbox hermes`, which needs the owner's OK.
- Flux substitution blanks `${VAR}` in manifests. Hermes config uses `${VAR}` in MCP headers, so annotate that ConfigMap `kustomize.toolkit.fluxcd.io/substitute: disabled`, and write `$VAR` in Sandbox shell scripts.
- gVisor breaks `kubectl port-forward`, so test from inside the pod.
- Don't pass verbose flags that could write prompts or keys to Loki.
- Agent-sandbox creates no default policy for a standalone Sandbox. Confirm that with `kubectl get networkpolicy` after deploying.

## Step 1: gVisor spike (throwaway)

This is the main risk, and it comes first. gVisor's filesystem blocked the OpenClaw 2026.9 upgrade, so nothing else gets built until this passes. Use a temporary namespace and PVC with the final security context, and remove them afterwards.

Open questions it must answer:

1. **Start as non-root.** The image's `USER` is root, with s6-overlay `/init` as PID 1 and a privilege drop to uid 10000. Its scripts handle a non-root start, but one comment mentions setuid `preinit`, which `allowPrivilegeEscalation: false` may block. Try uid 10000 with `fsGroup` 10000.
2. **SQLite WAL** in `/opt/data/state.db` on `ceph-block` under gVisor, including restarting with existing data. If WAL misbehaves, set `database.journal_mode: delete`.
3. **Gateway start, restart and child reaping** under gVisor, and whether a read-only root filesystem works. OpenClaw needed it writable, so writable is acceptable if necessary.
4. **Tool calling against the Spark**, with MCP tools loaded and thinking on, plus a real Telegram exchange using a test bot if available.
5. **Web access:** how Hermes fetches a URL without a paid backend, since the briefing needs two Google News RSS feeds. Also whether it can search without a paid key. Fetching is required, and search isn't.
6. **Contention with site summaries:** run long Hermes turns while the forecast API generates, and compare summary latency and errors with its baseline. Its handler timeout is 120 s, and it generates every 30 minutes. Use this to set Hermes's concurrency and context limits. It can run from the test pod, or from anywhere that exercises the same Spark server with a similar load.

**Stop and go back to the owner if** a non-root start under gVisor can't work, or if it would take `allowPrivilegeEscalation: true` or running the agent as root. Don't loosen the sandbox to make it pass, and don't bypass `/init`, which the docs say breaks the gateway.

## Step 2: deploy beside OpenClaw

Open as PRs and let the owner merge. Secrets the owner needs to supply: new Telegram bot token, owner ID, and any new MCP tokens.

## Step 3: the briefing

Schedule 06:45 `America/Los_Angeles`, the same as OpenClaw's, delivered to the Hermes bot, so the two can be compared directly. Keep OpenClaw's running throughout.

Content, from OpenClaw's job:

1. One-line greeting and the date, in plain text for Telegram with no tables.
2. Weather from lfpweather: overnight low, high so far, current temperature, wind, rain.
3. Air quality from lfpweather: AQI with its label, and CO2.
4. Birds from lfpweather, last 24 hours: 3–5 notable species rather than top counts.
5. About 3 local and 3 national or world headlines from the Google News RSS feeds, with source.
6. One line on whether it's a good morning to open the windows.

It must never invent data. If a source fails, it says so briefly and carries on. Copy the exact prompt from the live OpenClaw job, adjusting tool names and removing personal details from anything committed.

The job must survive Sandbox regeneration. Hermes keeps cron jobs on the PVC, but the definition should be recoverable from git and re-applying it shouldn't create duplicates.

## Done means

**For the build:**

- [ ] Spike passed, and its findings are recorded in the app README.
- [ ] Pod healthy with the final security context, `apps-hermes` Ready.
- [ ] Telegram: the owner gets answers, and a message from another account is refused.
- [ ] Each MCP server's tools work: WSDOT, Home Assistant read, lfpweather.
- [ ] Allowed from inside the pod: public internet, the Spark on 8000, the three MCP services.
- [ ] **Denied** from inside the pod: olympic, the router, an iDRAC, the Spark on port 22, the Kubernetes API, a Ceph monitor, and an unrelated cluster service such as Grafana. Paste this evidence into the PR.
- [ ] Public hostnames of homelab services tested from the pod, and the result shown to the owner and recorded in the README.
- [ ] Nothing reachable inbound except what the policy names.
- [ ] Site summaries still generate without errors under real Hermes use.
- [ ] State survives `delete sandbox`: memory and the cron job both still exist.
- [ ] A VolSync backup has completed and a restore has been tested.
- [ ] HA's exposed entities have been shown to the owner.
- [ ] OpenClaw still works, and its digest still arrives.

**For the switch decision, after the build:** the owner has seen a few good briefings side by side and is happy with Telegram answers. Then the owner decides. A later project can turn off OpenClaw's briefing, and separately settle Renovate approvals.

## Rollback

Set the `apps-hermes` Kustomization aside or revert the PRs. OpenClaw is untouched throughout, so there's nothing to restore. Keep `hermes-data` until the owner decides otherwise.

## Risks accepted

- **Prompt injection.** Open internet plus local tools is the classic path, and the policy limits where Hermes connects, not what it does with access it holds. Home Assistant is the highest-impact tool. This model is less proven against injection than Claude.
- **Token exposure.** An injected Hermes could likely exfiltrate its tokens, as OpenClaw could. The response is to revoke them, so the README must list each token and how to rotate it.
- **Persistent memory.** Hermes writes its own memory and skills, so an injected instruction could persist. They stay on the PVC, with backups as the way back. The switch should weigh this.
- **Spark availability.** The agent and site summaries share one box with an untested reboot. If the Spark is down, Hermes can't answer. `SparkInferenceDown` covers alerting. A reboot test is worth doing before relying on it, and needs the owner's OK.
- **Answer quality.** The Spark model may not match Claude Sonnet, which is what the side-by-side period is for.

## Left to the builder

- Exact Hermes config keys, verified against the pinned version.
- CPU and memory requests and limits. OpenClaw idles at about 300m and 560Mi, and Hermes's image is about 1 GiB. Kyverno requires requests on the pod.
- Health probes, if the gateway exposes something usable without opening inbound access.
- How the cron definition is declared, for example OpenClaw's sidecar pattern or a seed file.
- VolSync schedule.
- Order and split of PRs, keeping each one independently revertible.
