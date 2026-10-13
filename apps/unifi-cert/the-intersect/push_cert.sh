#!/bin/sh
# Push the cert-manager certificate for the UDM Pro SE console onto the UDM.
#
# UniFi OS has no API for a custom certificate: its nginx serves 443 and 8444
# from /data/unifi-core/config/unifi-core.{crt,key} (see
# /data/unifi-core/config/http/local-certs.conf on the console). This compares
# the SHA-256 of the full chain in the mounted secret with the file on the
# UDM; if they differ it writes the key and chain over SSH, keeps a one-time
# copy of the original self-signed pair, and restarts unifi-core so nginx picks
# them up. The restart briefly interrupts the console UI and API; routing and
# the Network application keep running. Idempotent.
#
# alpine/git ships ssh and busybox sha256sum but no openssl, hence the file
# hash rather than a served-certificate fingerprint. The root filesystem is
# read-only and HOME is an emptyDir.
#
# Env: UDM_HOST. Files: /tls/tls.crt, /tls/tls.key, /ssh/id_ed25519,
# /ssh/known_hosts.
set -eu

: "${UDM_HOST:?}"
cfg=/data/unifi-core/config

udm() {
  ssh -i /ssh/id_ed25519 \
    -o UserKnownHostsFile=/ssh/known_hosts -o StrictHostKeyChecking=yes \
    -o BatchMode=yes -o ConnectTimeout=15 \
    "root@$UDM_HOST" "$@"
}

want="$(sha256sum /tls/tls.crt | cut -d' ' -f1)"
have="$(udm "sha256sum $cfg/unifi-core.crt" | cut -d' ' -f1)"
echo "secret chain ${want%"${want#????????????????}"}  UDM file ${have%"${have#????????????????}"}"
if [ "$want" = "$have" ]; then
  echo "UDM already has the current certificate; nothing to do"
  exit 0
fi

echo "keeping the original self-signed pair as unifi-core.*.selfsigned (first run only)"
udm "test -e $cfg/unifi-core.crt.selfsigned || { cp -p $cfg/unifi-core.crt $cfg/unifi-core.crt.selfsigned && cp -p $cfg/unifi-core.key $cfg/unifi-core.key.selfsigned; }"

echo "writing key and certificate chain"
udm "umask 077 && cat > $cfg/unifi-core.key.new" < /tls/tls.key
udm "umask 022 && cat > $cfg/unifi-core.crt.new" < /tls/tls.crt
udm "mv $cfg/unifi-core.key.new $cfg/unifi-core.key && mv $cfg/unifi-core.crt.new $cfg/unifi-core.crt"

echo "restarting unifi-core so nginx serves the new certificate"
udm "systemctl restart unifi-core"

got="$(udm "sha256sum $cfg/unifi-core.crt" | cut -d' ' -f1)"
if [ "$got" != "$want" ]; then
  echo "certificate on the UDM does not match after the push" >&2
  exit 1
fi
echo "pushed; unifi-core restarted"
