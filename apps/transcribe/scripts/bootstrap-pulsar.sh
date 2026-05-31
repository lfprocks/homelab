#!/usr/bin/env bash

# bootstrap-pulsar.sh — idempotent one-shot for the Pulsar resources transcribe needs.
#
# The transcribe binary publishes nack'd messages exceeding PULSAR_MAX_DELIVERIES
# (default 5) to PULSAR_DLQ_TOPIC (default public/transcribe/file-queue-dlq). The
# Pulsar broker doesn't auto-create that topic — without it the DLQ producer crashes
# at startup with "no partitioned metadata for topic", spamming ERROR logs every
# few seconds. This script ensures the namespace + topic exist.
#
# Re-runs are safe: each pulsar-admin command is gated on existence so already-
# provisioned resources don't error.
#
# Prereqs:
#   - kubectl context pointed at the cluster running Pulsar
#   - pulsar-toolset-0 pod present in the `pulsar` namespace
#
# Usage:
#   ./apps/transcribe/scripts/bootstrap-pulsar.sh

set -euo pipefail

PULSAR_NS="pulsar"
TOOLSET_POD="pulsar-toolset-0"
TENANT="public"
NAMESPACE="transcribe"
DLQ_TOPIC="file-queue-dlq"

run_admin() {
  kubectl exec -n "$PULSAR_NS" "$TOOLSET_POD" -- bin/pulsar-admin "$@"
}

echo "==> Ensuring Pulsar namespace ${TENANT}/${NAMESPACE} exists"
if run_admin namespaces list "$TENANT" | grep -qx "${TENANT}/${NAMESPACE}"; then
  echo "    namespace ${TENANT}/${NAMESPACE} already present"
else
  run_admin namespaces create "${TENANT}/${NAMESPACE}"
  echo "    created ${TENANT}/${NAMESPACE}"
fi

echo "==> Ensuring DLQ topic ${TENANT}/${NAMESPACE}/${DLQ_TOPIC} exists"
TOPIC_FQDN="persistent://${TENANT}/${NAMESPACE}/${DLQ_TOPIC}"
if run_admin topics list "${TENANT}/${NAMESPACE}" | grep -qx "$TOPIC_FQDN"; then
  echo "    topic $TOPIC_FQDN already present"
else
  # Non-partitioned: DLQ is intentionally low-volume (only poison messages land here),
  # so the operational simplicity of a single-partition topic outweighs any throughput
  # concern. Convert to partitioned later via `topics create-partitioned-topic` if
  # message volume ever justifies it.
  run_admin topics create "$TOPIC_FQDN"
  echo "    created $TOPIC_FQDN"
fi

echo "==> Done. Verify via:"
echo "    kubectl exec -n ${PULSAR_NS} ${TOOLSET_POD} -- bin/pulsar-admin topics list ${TENANT}/${NAMESPACE}"
