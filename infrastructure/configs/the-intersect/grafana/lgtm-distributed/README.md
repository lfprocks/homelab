# Grafana alerts

This document tells you how to make an alert rule. It also tells you how to
make sure that the alert rule operates correctly.

Git controls all of the alert configuration. Do not make alert rules in the
Grafana user interface.

## Location of the configuration

The configuration is in `values.yaml`, below `grafana.alerting`. There are three
files. The chart writes these files into the Grafana container.

| File | Content |
| --- | --- |
| `contactpoints.yaml` | The destination of a notification and its format |
| `policies.yaml` | The contact point that receives each alert |
| `rules.yaml` | The alert rules |

The credentials are in `alerting-secret.sops.yaml`. SOPS encrypts this file with
the age recipient of this repository. Grafana reads the credentials as
environment variables. The `grafana.envFromSecret` value makes this connection.

## How the system sends a notification

There is one contact point. Its name is `pnp-pushover`. It is a webhook that
sends data to the
[pulsar-notifcation-pipeline](https://github.com/michaelpeterswa/pulsar-notifcation-pipeline).

The notification moves through four components:

1. Grafana sends the notification to `pnp-writer`.
2. `pnp-writer` puts the notification on a Pulsar topic. Pulsar encrypts it.
3. `pnp-deliverer` reads the notification from the topic.
4. `pnp-deliverer` sends the notification to Pushover.

The webhook URL is `http://pnp-writer.pnp.svc.cluster.local/notifications`. This
is the in-cluster address. Thus the notification data stays in the cluster. The
notification does not use the gateway or external DNS.

`pnp-writer` reads a token file to authenticate each client. The token for
Grafana is the `grafana-alerts` entry in
[`writer_secret.yaml`](../../../../../apps/pulsar-notification-pipeline/the-intersect/writer_secret.yaml).

Make sure that the two copies of the token are the same. One copy is in
`writer_secret.yaml`. The other copy is in `alerting-secret.sops.yaml`. If you
change only one copy, `pnp-writer` gives a 401 error. Grafana sends no
notifications.

`pnp-writer` reads its token file one time at start. Thus a new token has no
effect until the pod starts again. The `reloader.stakater.com/auto` annotation
starts the pod again automatically.

## How to add an alert rule

1. Add the rule to `alerting.rules.yaml.groups` in `values.yaml`. Put the rule
   in a group that has the name of the subject, for example `battery`. Do not
   make groups that have the name of a severity.

   ```yaml
   - orgId: 1
     name: battery
     folder: alerts
     interval: 5m
     rules:
       - uid: battery-midpoint-deviation
         title: BatteryMidpointDeviation
         condition: fired
         for: 15m
         annotations:
           title: battery bank midpoint is drifting
           summary: One half of the string is different from the other half.
         labels:
           severity: warning
         data:
           - refId: deviation
           - refId: worst
           - refId: fired
   ```

   The `uid` value is the identity of the rule. Keep this value constant.

   The `title` value is the name that `policies.yaml` uses to find the rule.

   The `condition` value is the `refId` of the last expression.

2. Add a `title` annotation and a `summary` annotation. The contact point uses
   these two values for the text of the notification. If you do not add them,
   the notification contains only the name of the alert rule.

3. Add a route to `policies.yaml`. The route sends the alert to the contact
   point.

   ```yaml
   - receiver: pnp-pushover
     object_matchers:
       - ["alertname", "=", "BatteryMidpointDeviation"]
     group_by: ["alertname"]
     group_wait: 5m
     group_interval: 30m
     repeat_interval: 24h
   ```

   Do not forget this step. If the alert rule has no route, Grafana sends the
   alert to `grafana-default-email`. That contact point does not send a
   notification. The Grafana user interface shows the alert rule in the fired
   condition. This failure gives no error message.

4. Set the interval values to agree with the type of the condition.

   - A condition that occurs one time needs a short `group_wait` value.
   - A condition that continues for hours needs a long `repeat_interval` value.
     If the value is too short, the system sends too many notifications.

## Four errors that occurred before

### Helm reads the values file as a template

The chart applies the Helm `tpl` function to these values. Thus Helm reads
`{{ }}` characters at render time. The upgrade then stops with this message:

```
nil pointer evaluating interface {}.Firing
```

Grafana must read the `{{ }}` characters at alert time. Put these characters in
a backtick block. Helm then writes the characters without a change.

```yaml
template: |
  {{ `{ "title": {{ .CommonLabels.alertname | toJson }} }` }}
```

### The `message` field is not correct for the body

Put the body of the notification in the `payload.template` field. Do not use the
`message` field.

The `message` field writes text into one field of the fixed JSON body of
Grafana. `pnp-writer` refuses that body with a 400 error. The
`payload.template` field replaces the full body.

### Credentials must be references

Write each credential as a `$__env{}` reference. Do not write the value of a
credential in `values.yaml`.

The chart writes `values.yaml` into a ConfigMap. Thus any person with access to
the cluster can read a credential value with `kubectl get configmap`. Grafana
reads a `$__env{}` reference from the environment when it sends a notification.

### A datasource UID can change

An alert rule refers to a datasource by its UID. The `prom`, `loki` and `tempo`
datasources are in git. Their UIDs are constant.

The battery rule uses the UID `aeiuudh9knklcd`. This is the `TimescaleDB`
datasource. A person made this datasource in the user interface. If a person
makes it again, the UID changes. The alert rule then stops. This failure gives
no error message.

Use a datasource from git for each new alert rule.

## How to select a threshold value

Measure the data first. Do not estimate the threshold value.

Query the data for a period that contains the correct condition and the
incorrect condition. Then put the threshold value between the two sets of
values.

These are the measurements for the battery rule:

| Pack voltage | Mean absolute deviation | Maximum |
| --- | --- | --- |
| 26.8–27.1 V (no charge) | 0.003–0.023 V | 0.03 V |
| 27.6 V (voltage increases) | 0.184 V | 0.51 V |
| **28.3 V (absorption)** | **0.52–0.58 V** | **0.69 V** |
| 26.9 V (charge complete) | 0.048–0.086 V | 0.14 V |

There are two sets of values. There are no values between 0.184 V and 0.52 V.
Thus the threshold value is 0.35 V.

### Do not use an alarm flag from a device

The SmartShunt has a `mid voltage` alarm flag. Do not use this flag as the
condition of an alert rule.

The device sets and clears the flag with its own hysteresis values. You cannot
see these values. The flag became clear at a deviation of −0.550 V. The flag was
set at a deviation of −0.510 V. Thus an alert rule that uses the flag changes
condition frequently.

Calculate the condition from the measurements instead.

### Use the `max` reducer

Use `max` in the `reduce` expression. Do not use `mean`.

A window of 30 minutes usually contains a long period with no charge. The `mean`
reducer decreases a correct high value to an incorrect low value. Use the `for`
field to prevent notifications from short changes.

## How to make sure that an alert rule operates

This repository has no CI. Thus these are the only checks. Do all of them.

1. Make sure that the configuration builds.

   ```sh
   kustomize build infrastructure/configs/the-intersect/grafana/lgtm-distributed/
   ```

2. Make sure that Helm renders the configuration. This check finds the template
   error.

   ```sh
   helm template lgtm grafana/lgtm-distributed --version 2.1.0 \
     -f infrastructure/configs/the-intersect/grafana/lgtm-distributed/values.yaml \
     >/dev/null
   ```

   Read the output of this command. The output shows the payload template in the
   same format that Grafana reads. This is the only method to make sure that the
   `{{ }}` characters are correct.

3. Make sure that Grafana accepted the configuration.

   ```sh
   kubectl -n grafana logs deploy/lgtm-distributed-grafana -c grafana \
     | grep provisioning.alerting
   ```

   The output must contain `finished to provision alerting`. There must be no
   error message before that line.

### Make sure that Grafana has the credentials

Do this check after each change to the secret. If the credentials are not
correct, the system gives no error message.

```sh
kubectl -n grafana exec deploy/lgtm-distributed-grafana -c grafana -- \
  sh -c 'echo "token=${#GRAFANA_PNP_TOKEN} pushover=${#GRAFANA_PUSHOVER_USER_KEY}"'
```

The output must be `token=48 pushover=30`.

A result of `token=0` shows that the secret is absent. Grafana then sends
alerts, and `pnp-writer` refuses each one with a 401 error.

### Test the full path

This test sends a notification. It uses the same credentials and the same path
as an alert rule.

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

A result of `202` with a `notification_id` value shows that `pnp-writer`
accepted the notification. This result does not show that Pushover received the
notification.

Find the same `notification_id` value in the log of `pnp-deliverer`:

```sh
kubectl -n pnp logs deploy/pnp-deliverer | grep <notification_id>
```

The log must contain `"status":"delivered"`. It must also contain a
`provider_response` value of `{"status":1}`.

Use a different `idempotencyKey` value for each test. `pnp-writer` keeps each
key for 5 minutes. If you use a key two times, `pnp-writer` gives a result of
`202` but does not send a notification.

## Other data

- **A route that is absent gives no error message.** The alert rule operates,
  and the user interface shows the fired condition. But the system sends no
  notification. Examine `policies.yaml` first when a notification is absent.
- **All alert rules use the same contact point.** Thus the payload template
  must be general. Do not put the name of one alert rule in the template.
- **The `uid` value is the identity of an alert rule.** You can change the
  `title` value. If you change the `uid` value, Grafana makes a new rule. The
  old rule keeps its silences and its condition.
- **You cannot change a provisioned object in the user interface.** If Grafana
  refuses a change to an alert rule, that rule is in `values.yaml`. Make the
  change there.
- **Git does not contain all of the Grafana configuration.** A person made some
  contact points, one alert rule and all of the dashboards in the user
  interface. These objects are only in the file `/var/lib/grafana/grafana.db`.
  There is no other copy. Put each new object in `values.yaml`.
