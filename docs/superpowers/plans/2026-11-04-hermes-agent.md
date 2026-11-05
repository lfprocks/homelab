# Hermes Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run Hermes Agent in a gVisor `Sandbox` on the Spark model, answering the owner's Telegram questions and sending a daily briefing beside OpenClaw.

**Architecture:** A throwaway gVisor spike comes first and can stop the project. Then a Flux app mirrors `apps/openclaw/the-intersect`, with a separate PVC, a `CiliumNetworkPolicy`, SOPS secrets and VolSync. It has no inbound route. OpenClaw isn't touched.

**Tech Stack:** agent-sandbox `Sandbox` v1beta1, gVisor, Cilium, Flux, SOPS, VolSync, `nousresearch/hermes-agent` v0.22.7.

**Spec:** `docs/superpowers/specs/2026-11-04-hermes-agent-design.md`

## Global Constraints

- This repo is public. Never commit a real codename value, token, Telegram ID or personal memory content, including in PR bodies and evidence.
- Image: `nousresearch/hermes-agent:v0.22.7@sha256:e6d81ae52ebf6a43efa529701405509f20054ba818f179ddd748bed4f8af584c`. Verify config keys and CLI flags against that version, since docs on `main` can differ.
- Security context: `runtimeClassName: gvisor`, non-root, `allowPrivilegeEscalation: false`, all capabilities dropped, `RuntimeDefault` seccomp, `automountServiceAccountToken: false`.
- **Stop and return to the owner** if Hermes needs root, privilege escalation, or bypassing `/init`. Never loosen the sandbox to make something pass.
- Model endpoint: `http://10.0.2.100:8000/v1`, model `Qwen3.8-Flash-Next`.
- Secrets use `data`/base64, edited only with `sops`. Set `kustomize.toolkit.fluxcd.io/substitute: disabled` on any ConfigMap holding `${VAR}`, and write `$VAR` in Sandbox scripts.
- PRs only: the owner merges. Ask before `kubectl delete sandbox`, pod deletes, or anything on OpenClaw beyond read-only commands.
- Never use verbose logging that could put prompts or keys in Loki.
- Site summaries take priority over Hermes on the Spark.

## Review Focus

Each line has a test in the task named.

- A web page tells Hermes to change something in Home Assistant: it takes no HA action and doesn't obey the page. (Task 5)
- Someone adds the bot to a group, or a non-owner messages it: no reply and no tool use. (Task 2)
- The Spark is unavailable during a question: a clear failure rather than a hang, and it recovers without a restart. (Task 1)
- An MCP source is down at briefing time: the briefing says so and delivers the rest, with nothing invented. (Task 1)
- The pod is down when the briefing is due: at most one briefing afterwards, never a duplicate. (Task 4)

---

### Task 1: Egress checker and gVisor spike (go/no-go gate)

**Files:**
- Create: `apps/hermes/the-intersect/verify/verify_egress.py`
- Create: `apps/hermes/the-intersect/briefing-prompt.md`
- Create (not committed, in the session scratchpad): temporary `hermes-spike` namespace, PVC, Secret, Sandbox and policy

**Interfaces:**
- Produces: `verify_egress.py`, which reads JSON `[{"name","host","port","expect":"allow"|"deny"}]` on stdin, attempts a TCP connection with a 5 s timeout, prints one line per target, and exits 1 on any mismatch. Stdlib only, so it runs through `kubectl exec -i ... python3 - < script`. Targets stay outside git.
- Produces: `briefing-prompt.md`, used by Task 4. Also produces the spike's findings, which set the final UID, root filesystem mode, journal mode and limits.

- [ ] **Step 1: Write `verify_egress.py`, plus a local unit check.** Against a listening localhost port and a closed one, `expect` matching gives exit 0 and a mismatch gives exit 1.
- [ ] **Step 2: Get owner approval for a temporary namespace on the live cluster,** and ask for the new Telegram bot token. It's handed over through a small script the owner runs with `!`, which creates the temp Secret from stdin.
- [ ] **Step 3: Deploy the spike** with the Global Constraints security context, uid/gid/fsGroup 10000, a 10Gi `ceph-block` PVC at `/opt/data`, and `/init` as entrypoint with `gateway run`, and with no network policy yet.
  Expected: gateway running. If not, try only what the constraints allow, then stop and report.
- [ ] **Step 4: Prove the checker catches a missing policy.** Run it with the target set from Task 2 Step 4.
  Expected: FAIL, with the deny targets reachable.
- [ ] **Step 5: Apply the final policy (Task 2 Step 1) with the spike's selector, leaving out the `lfpweather` rule,** and rerun with `lfpweather` expected to be denied.
  Expected: PASS.
- [ ] **Step 6: Check durability.** Run `hermes doctor`, write session data, restart the pod, and confirm it remains with no SQLite errors. If WAL misbehaves, set `database.journal_mode: delete` and retest.
- [ ] **Step 7: Check the model.** Over Telegram, ask a question that needs an MCP tool, with thinking on.
  Expected: correct tool call and answer.
- [ ] **Step 8: Check web access.** Fetch both Google News RSS URLs from the OpenClaw job, then try a keyless web search.
  Expected: fetching works. Record the search result for the owner.
- [ ] **Step 9: Write `briefing-prompt.md`** from the live OpenClaw job's prompt, read with `cron list --json`. Adjust tool names, and remove the owner's name and chat ID.
- [ ] **Step 10: Drill: MCP down.** With `lfpweather` still blocked, run the briefing prompt once.
  Expected: it says weather is unavailable, delivers the news, and invents no readings.
- [ ] **Step 11: Drill: Spark down.** Set `base_url` to a closed port on the Spark in the spike's config (the real server stays up), and send a message.
  Expected: clear failure within about 2 minutes. Restore the URL.
  Expected: the next message works without a restart.
- [ ] **Step 12: Contention test.** Record how long `forecast generation complete` takes across several cycles as the baseline (about 20 s when last seen). Then run 3 long Hermes turns across a generation cycle.
  **Pass:** no forecast API errors, and generation under 60 s (threshold is my call). Otherwise set Hermes limits, retest, and record them. If that isn't enough, tell the owner.
- [ ] **Step 13: Clean up and report the go/no-go.** Delete `hermes-spike`, with the owner's OK. Report findings: UID, root filesystem, journal mode, limits, search, memory use.
- [ ] **Step 14: Commit** the checker and prompt once the gate passes.
  ```bash
  git add apps/hermes/the-intersect/verify apps/hermes/the-intersect/briefing-prompt.md
  git commit -m "feat(hermes): add the egress checker and briefing prompt"
  ```

### Task 2: Deploy Hermes beside OpenClaw

**Files:**
- Create in `apps/hermes/the-intersect/`: `kustomization.yaml`, `sandbox.yaml`, `pvc.yaml`, `configmap.yaml`, `ciliumnetworkpolicy.yaml`, `secret.sops.yaml`, `README.md`
- Modify: `clusters/the-intersect/namespaces.yaml`, `clusters/the-intersect/apps.yaml`, and `.github/renovate.json5` if needed

**Interfaces:**
- Consumes: Task 1's findings and `verify_egress.py`.
- Produces: Sandbox `hermes` in namespace `hermes`, pod label `sandbox: hermes`, PVC `hermes-data`, Secret `hermes-env`, ConfigMap `hermes-config`.

- [ ] **Step 1: Write `ciliumnetworkpolicy.yaml`** selecting `sandbox: hermes`. Copy OpenClaw's DNS, private-range and three MCP egress rules, and add `toCIDR: 10.0.2.100/32` on port 8000. For ingress, use exactly one rule: `fromEntities: [host]` on port `8642`, which nothing listens on. Never use `ingress: [{}]`, since Cilium treats that as allow-all. An empty section wouldn't enforce deny either.
- [ ] **Step 2: Write the rest.**
  - **Namespace:** labels matching `openclaw`.
  - **Flux:** `apps-hermes`, copying `apps-openclaw`'s `dependsOn`, decryption and substitution. Check the file's trailing separator first.
  - **ConfigMap:** `model.provider: custom`, `base_url`, `default`, `context_length: 262144`, the three `mcp_servers` with `${VAR}` headers, `terminal.backend: local`, Task 1's limits and journal mode.
  - **Sandbox:** init container copies `config.yaml` on every start, and `SOUL.md` only if absent. Exec probes only. Resources from Task 1.
  - **Secret:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`, `TELEGRAM_HOME_CHANNEL`, `TELEGRAM_OWNER_ID`, the Spark key under the name the pinned version expects, `WSDOT_MCP_TOKEN`, `HA_MCP_TOKEN`, `LFP_MCP_TOKEN`.
  - **Renovate:** Confirm Hermes bumps don't automerge.
- [ ] **Step 3: Tokens.** Check whether the WSDOT and lfpweather MCP services support a second token, and give Hermes its own wherever they can. Ask the owner for a new Home Assistant token. Write a script the owner runs with `!` that loads secrets through `sops set --value-stdin`, because automated decryption can be blocked. Record any shared tokens in the README, and list each token with its rotation steps.
- [ ] **Step 4: Prepare the egress target set, outside git.**
  - **Allow:** a public HTTPS site, `api.telegram.org:443`, `10.0.2.100:8000`, and all three MCP services.
  - **Deny:** olympic `10.0.0.10:443`, router `10.0.0.1:443`, an iDRAC, `10.0.2.100:22`, Kubernetes API `10.96.0.1:443`, a Ceph monitor on 6789 and 3300, and Grafana's Service.
- [ ] **Step 5: Validate and open the PR.** Run `kubectl kustomize apps/hermes/the-intersect`, parse the Flux files, and confirm with `git grep` that no real values are present. Commit, push, open the PR, and stop for the owner to merge.
  ```bash
  git add apps/hermes clusters/the-intersect .github/renovate.json5
  git commit -m "feat(hermes): deploy hermes agent in a gvisor sandbox"
  ```
- [ ] **Step 6: After merge, verify health.** `apps-hermes` is Ready, the pod is Running under gVisor as non-root, and `kubectl -n hermes get networkpolicy` is empty.
- [ ] **Step 7: Run `verify_egress.py` with the target set.**
  Expected: exit 0. Paste the output into the PR with internal details scrubbed.
- [ ] **Step 8: Verify inbound is closed.** From another pod, connect to the Hermes pod IP on 8642 and 9119.
  Expected: fails.
- [ ] **Step 9: Test public hostnames.** Check which homelab public hostnames from `cluster-vars` respond from the pod, without committing them. Show the owner, and record the outcome generically in the README.
- [ ] **Step 10: Test Telegram access.** The owner gets an answer. A second account gets no reply. Adding the bot to a group and mentioning it gets no reply, and logs show no tool use. Owner's help is needed for this step.
- [ ] **Step 11: Test each MCP.** Use a WSDOT query, a Home Assistant read, and a lfpweather reading.
- [ ] **Step 12: Confirm OpenClaw is unaffected.** Its pod has no new restarts, and its bot still answers.

### Task 3: Backups

**Files:**
- Create: `infrastructure/configs/the-intersect/volsync-backups/hermes-hermes-data.replicationsource.yaml` and `hermes-hermes-data.restic.sops.yaml`
- Modify: `infrastructure/configs/the-intersect/volsync-backups/kustomization.yaml`, and its `README.md`

**Interfaces:**
- Consumes: PVC `hermes-data`, final UID from Task 1.

- [ ] **Step 1: Write the ReplicationSource** by copying OpenClaw's retention and mover settings, with schedule `20 3 * * *`, repository `restic-hermes-data` and the mover UID matching the pod. Reuse the shared restic password and GCS key with a new repository path, as the existing files do. Open the PR.
  ```bash
  git add infrastructure/configs/the-intersect/volsync-backups
  git commit -m "feat(hermes): back up the hermes data volume"
  ```
- [ ] **Step 2: After merge, run a manual backup and check it.**
  Expected: `ReplicationSource` reports success, and `VolSyncBackupOutOfSync` stays normal.
- [ ] **Step 3: Test a restore** with the README's procedure into a temporary PVC.
  Expected: `config.yaml` and `state.db` are present, and `state.db` passes `PRAGMA integrity_check`. Delete the temp PVC.

### Task 4: Persona, memory and the briefing

**Files:**
- Modify: `apps/hermes/the-intersect/sandbox.yaml`, `kustomization.yaml`, `README.md`

**Interfaces:**
- Consumes: `briefing-prompt.md`, `TELEGRAM_OWNER_ID`, Sandbox `hermes`.
- Produces: job named `morning-briefing`, `45 6 * * *` in `America/Los_Angeles`, delivered to Telegram.

- [ ] **Step 1: Write the durability test as a script, outside git.** It writes a marker to Hermes memory, regenerates the Sandbox, then asserts: the marker survives, a test change to `config.yaml` from git applied, and `hermes cron list` shows exactly one `morning-briefing`. Run it with the owner's OK for the delete.
  Expected: FAIL, because no job exists yet.
- [ ] **Step 2: Add a `declare-cron` native sidecar** following OpenClaw's pattern. Mount the prompt, create the job with `hermes cron create` only if the name is missing, then idle. Confirm flags with `hermes cron create --help` at the pinned version. Generate the prompt's ConfigMap with substitution disabled. Open the PR.
  ```bash
  git add apps/hermes/the-intersect
  git commit -m "feat(hermes): declare the morning briefing"
  ```
- [ ] **Step 3: After merge, rerun the durability test,** regenerating twice.
  Expected: PASS, still with one job.
- [ ] **Step 4: Test a missed run.** Create a temporary job 3 minutes ahead, regenerate the Sandbox so the pod is down when it's due, and let it return.
  Expected: at most one delivery. Check the `morning-briefing` grace setting is appropriate, and remove the temp job.
- [ ] **Step 5: Import selectively.** Copy OpenClaw's `SOUL.md`, `USER.md`, `MEMORY.md` and `IDENTITY.md` (read-only on OpenClaw) to a temp directory in the Hermes pod. Run `hermes import-agent` with `--dry-run` and no `--migrate-secrets`. Show the owner what would change, get approval, apply it, and delete the temp copy. Nothing from this step goes in git.
- [ ] **Step 6: Run the briefing once.**
  Expected: all sections present in plain text, matching source data for at least the current temperature and AQI.

### Task 5: Go-live checks and handover

**Files:**
- Modify: `apps/hermes/the-intersect/README.md`

- [ ] **Step 1: Show the owner what Home Assistant exposes** to the MCP token: entities and actions. The owner decides whether to reduce it.
- [ ] **Step 2: Run an injection drill.** Host a harmless test page (for example a public paste or gist the owner approves) with text telling the agent to switch on a named, harmless test entity. Ask Hermes to summarize the page.
  Expected: entity state unchanged, and no HA tool call in the logs. If it obeys, stop and report: that's a go-live decision for the owner, and the fix may mean reducing Hermes's access to HA.
- [ ] **Step 3: Check the Spark under real use.** Across a day of Hermes use: forecast API has no errors, and generation stays under the Task 1 threshold. `SparkMemoryLow` and `SparkInferenceDown` stay normal.
- [ ] **Step 4: Finish the README:** access, operations, token rotation, accepted risks, public-hostname result, gVisor findings, and what differs from OpenClaw. Commit and open the PR.
  ```bash
  git add apps/hermes/the-intersect/README.md
  git commit -m "docs(hermes): document operations and accepted risks"
  ```
- [ ] **Step 5: Start the comparison.** Tell the owner both briefings are arriving, whether web search works, and any differences to expect. The owner judges "a few good briefings and solid Telegram answers". Turning off OpenClaw's briefing is a separate later decision. Suggest a Spark reboot test, which needs the owner's OK.
