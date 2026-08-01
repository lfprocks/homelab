# Grafana alerting

How alerts are defined, delivered, and — more importantly — how to tell whether
they actually work. Everything here is provisioned from git; nothing in this
document should be done in the Grafana UI.

## Where it lives

All of it is in `values.yaml`, under `grafana.alerting`, as three provisioning
files the chart writes into the Grafana container:

| Key | Holds |
| --- | --- |
| `contactpoints.yaml` | Where a notification goes and what it looks like |
| `policies.yaml` | Which alerts route to which contact point |
| `rules.yaml` | The alert rules themselves |

Credentials live in `alerting-secret.sops.yaml` (SOPS, the repo age recipient)
and reach Grafana as environment variables via `grafana.envFromSecret`.

## Delivery

One contact point, `pnp-pushover`. It is a plain webhook to
[pulsar-notifcation-pipeline](https://github.com/michaelpeterswa/pulsar-notifcation-pipeline):

```
grafana -> pnp-writer -> pulsar (E2EE) -> pnp-deliverer -> pushover
```

The URL is the in-cluster service, `http://pnp-writer.pnp.svc.cluster.local/notifications`,
so alert traffic never leaves the cluster and does not depend on the gateway or
on DNS outside it.

`pnp-writer` authenticates callers against a token file. Grafana's token is the
`grafana-alerts` entry in
[`apps/pulsar-notification-pipeline/the-intersect/writer_secret.yaml`](../../../../../apps/pulsar-notification-pipeline/the-intersect/writer_secret.yaml).
**Both halves must match**: the token in that file and the one in
`alerting-secret.sops.yaml`. Changing one without the other yields a 401 and no
notifications.

The writer loads its token file once at startup, so it carries
`reloader.stakater.com/auto`. A token added without a restart is a silent no-op.

## Adding an alert

Four edits, all in `values.yaml`, plus a route.

**1. Add the rule** under `alerting.rules.yaml.groups`. Group by subject
(`kyverno`, `battery`), not by severity.

```yaml
- orgId: 1
  name: battery
  folder: alerts
  interval: 5m
  rules:
    - uid: battery-midpoint-deviation     # stable; changing it orphans state
      title: BatteryMidpointDeviation     # what policies.yaml matches on
      condition: fired                    # refId of the final expression
      for: 15m
      annotations:
        title: battery bank midpoint is drifting   # pushover title
        summary: One half of the string is diverging under charge.
      labels:
        severity: warning
      data:
        - refId: deviation                # the query
        - refId: worst                    # reduce
        - refId: fired                    # threshold
```

**2. Give it `title` and `summary` annotations.** The contact point's payload is
deliberately generic and takes its wording from these. A rule without them
notifies with nothing but its `alertname`.

**3. Route it** in `policies.yaml`, matching on `alertname`:

```yaml
- receiver: pnp-pushover
  object_matchers:
    - ["alertname", "=", "BatteryMidpointDeviation"]
  group_by: ["alertname"]
  group_wait: 5m
  group_interval: 30m
  repeat_interval: 24h
```

Without a route, an alert fires into `grafana-default-email` and goes nowhere.
This is the most common way to build an alert that appears to work and never
notifies.

**4. Tune the intervals to the shape of the condition.** A discrete event
(an admission rejection) wants short waits and grouping by the labels that
distinguish instances. A continuous condition that persists for hours (a battery
imbalance during absorption) wants a long `repeat_interval`, or it becomes noise
and then gets ignored.

## Four traps, all of which have already bitten

**The chart runs `tpl` over these values.** Unescaped `{{ }}` is evaluated by
*Helm* at render time, and the upgrade dies with
`nil pointer evaluating interface {}.Firing`. Anything Grafana should evaluate
at alert time must be wrapped so Helm emits it verbatim:

```yaml
template: |
  {{ `{ "title": {{ .CommonLabels.alertname | toJson }} }` }}
```

**The body goes in `payload.template`, not `message`.** `message` only templates
a string field inside Grafana's own fixed JSON envelope, which `pnp-writer`
rejects with a 400. `payload.template` replaces the whole body.

**Credentials are `$__env{}` references, never literals.** These values are
rendered into a ConfigMap; anything written literally is committed in plaintext
and visible with `kubectl get configmap`. Grafana resolves `$__env{NAME}` from
the environment at use time.

**Datasource UIDs are referenced by value.** `prom`, `loki` and `tempo` are
provisioned and stable. `aeiuudh9knklcd` — the `TimescaleDB` datasource used by
the battery rule — was created by hand in the UI, so recreating it changes the
UID and the rule silently stops evaluating. Prefer provisioned datasources for
anything an alert depends on.

## Choosing a threshold

**Measure first.** Query the data over a period that contains both the healthy
and the faulty state, and put the threshold in the gap. The battery rule was set
this way:

| Pack voltage | avg abs deviation | max |
| --- | --- | --- |
| 26.8–27.1 V (at rest) | 0.003–0.023 V | 0.03 |
| 27.6 V (rising) | 0.184 V | 0.51 |
| **28.3 V (absorption)** | **0.52–0.58 V** | **0.69** |
| 26.9 V (charge over) | 0.048–0.086 V | 0.14 |

Two populations, an empty gap, threshold at 0.35 V. A guessed threshold would
have been somewhere in one population or the other.

**Prefer a computed threshold to a device's own alarm flag.** The SmartShunt
raises its own `mid voltage` alarm, and using it was the obvious shortcut. It
was rejected because it was observed *clearing* at a larger deviation (−0.550 V)
than it *set* at (−0.510 V): the device's internal hysteresis is not visible
here, so an alert built on the flag flaps for reasons nothing in this repo can
explain.

**`reduce` with `max`, not `mean`, for intermittent conditions.** A thirty
minute window that is mostly at rest will average a genuine excursion away.
`for:` is the right tool for suppressing noise, not the reducer.

## Verifying

There is **no CI in this repository**, so these are the only checks a change
gets. Run all of them.

```sh
# 1. does it still parse and build
kustomize build infrastructure/configs/the-intersect/grafana/lgtm-distributed/

# 2. does Helm render it -- this is the one that catches the tpl trap
helm template lgtm grafana/lgtm-distributed --version 2.1.0 \
  -f infrastructure/configs/the-intersect/grafana/lgtm-distributed/values.yaml \
  >/dev/null

# 3. did grafana accept the provisioning after it rolled out
kubectl -n grafana logs deploy/lgtm-distributed-grafana -c grafana \
  | grep provisioning.alerting
# expect: "finished to provision alerting", and no error above it
```

Rendering with `helm template` and then reading the output is worth the extra
minute: it shows the payload template exactly as Grafana will see it, which is
the only way to confirm the `{{ }}` survived Helm.

### Confirm the credentials actually arrived

This is the check that matters, because its absence is silent:

```sh
kubectl -n grafana exec deploy/lgtm-distributed-grafana -c grafana -- \
  sh -c 'echo "token=${#GRAFANA_PNP_TOKEN} pushover=${#GRAFANA_PUSHOVER_USER_KEY}"'
# expect: token=48 pushover=30
# token=0 means the secret is missing or not referenced -- alerts will fire
# and every one will be rejected with a 401
```

### End-to-end test

Sends a real notification, using the same credentials and path an alert takes:

```sh
kubectl -n grafana exec deploy/lgtm-distributed-grafana -c grafana -- sh -c '
KEY="test-$(date +%s)"
curl -s -w "\nhttp_status=%{http_code}\n" \
  -X POST http://pnp-writer.pnp.svc.cluster.local/notifications \
  -H "Authorization: Bearer $GRAFANA_PNP_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"originatingService\":\"grafana\",\"idempotencyKey\":\"$KEY\",
       \"content\":{\"title\":\"test\",\"body\":\"pipeline check\",\"priority\":0},
       \"pushover\":{\"userOrGroupKey\":\"$GRAFANA_PUSHOVER_USER_KEY\"}}"'
```

`202` with a `notification_id` means the writer accepted it. That is not proof
of delivery — trace the id the rest of the way:

```sh
kubectl -n pnp logs deploy/pnp-deliverer | grep <notification_id>
# expect: "status":"delivered" and a provider_response of {"status":1}
```

`idempotencyKey` must be unique; the writer deduplicates within a 5 minute
window, so a repeated key returns success and sends nothing.

## Things worth knowing before you need them

- **A missing route is the quietest failure.** The alert evaluates, fires, shows
  as firing in the UI, and notifies nobody. Check `policies.yaml` before
  suspecting the contact point.
- **The contact point payload is shared.** It is generic on purpose. A template
  that names one alert will describe every other alert incorrectly — this file
  previously hardcoded `kyverno blocked N admission(s)`.
- **`uid` is the rule's identity.** Changing `title` is safe; changing `uid`
  creates a new rule and abandons the old one's silences and state.
- **Provisioned objects are read-only in the UI.** If Grafana will not let you
  edit an alert, it is provisioned, and the edit belongs in `values.yaml`.
- **Not everything in this Grafana is provisioned.** Contact points, rules and
  all dashboards created by hand live only in Grafana's SQLite and are absent
  from git. `kubectl -n grafana exec ... -- ls /var/lib/grafana/grafana.db` is
  the whole backup story for those. Anything added should be provisioned here
  instead.
