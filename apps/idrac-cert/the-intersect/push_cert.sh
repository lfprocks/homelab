#!/usr/bin/env bash
# Push the cert-manager certificate for olympic's iDRAC into the iDRAC.
#
# Compares the SHA-256 fingerprint of the leaf certificate in the mounted
# secret with the one the iDRAC serves on 443. If they differ, uploads the
# private key and the leaf certificate with remote racadm (the only import
# path iDRAC7 firmware offers for an externally generated key) and resets the
# iDRAC so it starts serving the new certificate. Idempotent.
#
# Env: IDRAC_HOST, IDRAC_USER, IDRAC_PASSWORD. Files: /tls/tls.crt, /tls/tls.key.
set -euo pipefail

: "${IDRAC_HOST:?}" "${IDRAC_USER:?}" "${IDRAC_PASSWORD:?}"
if [[ "$IDRAC_PASSWORD" == "REPLACE_ME" ]]; then
  echo "IDRAC_PASSWORD is still the placeholder; set it in idrac-secret.yaml" >&2
  exit 1
fi

fingerprint() { openssl x509 -noout -fingerprint -sha256 | cut -d= -f2; }
want="$(fingerprint < /tls/tls.crt)"   # x509 reads only the first (leaf) cert
have="$(echo | openssl s_client -connect "$IDRAC_HOST:443" -servername "$IDRAC_HOST" 2>/dev/null | fingerprint)"
echo "secret cert ${want:0:23}  iDRAC serves ${have:0:23}"
if [[ "$want" == "$have" ]]; then
  echo "iDRAC already serves the current certificate; nothing to do"
  exit 0
fi

rac() { racadm -r "$IDRAC_HOST" -u "$IDRAC_USER" -p "$IDRAC_PASSWORD" --nocertwarn "$@"; }
work="$(mktemp -d -p "$HOME")"   # root fs is read-only; $HOME is an emptyDir
openssl x509 -in /tls/tls.crt -out "$work/leaf.pem"

echo "uploading private key"
rac sslkeyupload -t 1 -f /tls/tls.key
echo "uploading certificate"
rac sslcertupload -t 1 -f "$work/leaf.pem"
echo "resetting the iDRAC so it serves the new certificate (unreachable for a minute or two)"
rac racreset
