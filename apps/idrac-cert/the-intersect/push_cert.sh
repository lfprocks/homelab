#!/usr/bin/env bash
# Push the cert-manager certificate for olympic's iDRAC into the iDRAC.
#
# Compares the SHA-256 fingerprint of the leaf certificate in the mounted
# secret with the one the iDRAC serves on 443. If they differ, uploads the
# private key and the certificate with remote racadm (the only import path
# iDRAC7 firmware offers for an externally generated key) and resets the iDRAC
# so it starts serving the new certificate. Idempotent.
#
# The certificate goes up as the full chain from tls.crt, so the iDRAC can
# serve the Let's Encrypt intermediate; clients that don't fetch missing
# intermediates themselves (Go, curl on Linux) reject a leaf-only chain. If
# racadm doesn't accept the chain, it falls back to the leaf alone, which is
# what earlier versions uploaded. iDRAC7 confirms an accepted upload with
# "DH010: Reset iDRAC to apply new certificate", not the word "success".
#
# The root filesystem is read-only: no here-strings or heredocs (bash needs a
# temp file for them), and TMPDIR points at the writable $HOME emptyDir.
#
# Env: IDRAC_HOST, IDRAC_USER, IDRAC_PASSWORD. Files: /tls/tls.crt, /tls/tls.key.
set -euo pipefail
export TMPDIR="$HOME"

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
echo "uploading certificate chain"
out="$(rac sslcertupload -t 1 -f /tls/tls.crt 2>&1)" || true
echo "$out"
case "$out" in
  *DH010* | *[Ss]uccess*) ;;
  *)
    echo "chain upload not accepted; uploading the leaf certificate alone"
    rac sslcertupload -t 1 -f "$work/leaf.pem"
    ;;
esac
echo "resetting the iDRAC so it serves the new certificate (unreachable for a minute or two)"
rac racreset
