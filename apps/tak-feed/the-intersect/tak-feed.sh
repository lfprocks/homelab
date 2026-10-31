#!/bin/sh
# One-way pushes to the TAK server VM over SSH, run by the two CronJobs:
#
#   tak-feed.sh cert   the tak-tls certificate (mounted at /tls) into the
#                      VM's /etc/ots-tls, as fullchain.pem and privkey.pem
#   tak-feed.sh maps   the allowlisted ATAK outputs of the library share
#                      (mounted read-only at /library) into /srv/library
#
# The VM pins each key to one directory with a forced
# `rrsync -wo -munge <dir>` command (lfprocks/ansible, role opentakserver):
# write-only, no shell, symlinks munged. The host key is pinned too
# (StrictHostKeyChecking=yes against the known_hosts in the tak-feed-ssh
# Secret), so a rebuilt VM fails the push instead of receiving the key.
#
# The image has no rsync or ssh and the pod is not root, so both are
# installed into an emptyDir with apk's user mode on every run, from the
# image's own Alpine branch, signature-checked with its own keys.
set -eu

mode=${1:?usage: tak-feed.sh cert|maps}
: "${TAK_HOST:?}" "${TAK_PORT:?}"

umask 077
tools=/tools
apk add --quiet --no-cache --usermode --initdb --root "$tools" \
  --keys-dir /etc/apk/keys --repositories-file /etc/apk/repositories \
  rsync openssh-client-default

# ssh refuses a key another user can read; the Secret volume is root-owned.
cp /ssh/key /tmp/key
chmod 0600 /tmp/key

ssh_cmd="$tools/usr/bin/ssh -F /dev/null -i /tmp/key -p $TAK_PORT \
  -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/ssh/known_hosts -o GlobalKnownHostsFile=/dev/null \
  -o ConnectTimeout=30 -o ServerAliveInterval=30 -o ServerAliveCountMax=4"

# The tools' libraries only for the tools: python3 keeps the image's own.
rsync_() {
  LD_LIBRARY_PATH="$tools/usr/lib:$tools/lib" "$tools/usr/bin/rsync" -e "$ssh_cmd" "$@"
}

case "$mode" in
  cert)
    # One copy, taken once: what is checked is exactly what is sent, even
    # if cert-manager renews the Secret mid-run.
    mkdir /tmp/tls
    for f in fullchain.pem privkey.pem; do
      cp "/tls/$f" "/tmp/tls/$f"
      [ -s "/tmp/tls/$f" ] || { echo "refusing: /tls/$f is empty or missing" >&2; exit 1; }
    done
    # A key that does not match its certificate would leave nginx unable to
    # start at its next restart (its reload refuses the pair, a restart
    # fails), so it is never sent.
    python3 -c 'import ssl, sys; ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(sys.argv[1], sys.argv[2])' \
      /tmp/tls/fullchain.pem /tmp/tls/privkey.pem \
      || { echo "refusing: the key does not match the certificate" >&2; exit 1; }
    # --checksum: an unchanged certificate is not rewritten, so the VM's
    # path unit (and its nginx reload) fires only on a real renewal.
    rsync_ --checksum --perms --chmod=F600 --timeout=120 \
      --itemize-changes /tmp/tls/fullchain.pem /tmp/tls/privkey.pem "tak-feed@$TAK_HOST:/"
    ;;
  maps)
    # push.py refuses (non-zero) on a missing or empty share, so an unmounted
    # library never reaches --delete-after.
    python3 /app/push.py /library /tmp/stage
    # Again, immediately before rsync follows the links (--copy-links):
    # every link must still lead, through no symlink, to a regular file at
    # its own path under /library. The share is mounted without mfsymlinks,
    # so a link written through SMB is a plain file here; this is the
    # second wall, for anything that still shows up as a symlink.
    python3 /app/push.py --verify /library /tmp/stage
    # The stage holds one symlink per allowlisted file and nothing else, so
    # --delete-after leaves the server holding exactly the allowlisted set.
    # lost+found (the VM disk's root) is excluded, which also protects it.
    rsync_ --recursive --times --copy-links --perms --chmod=D755,F644 \
      --delete-after --exclude=/lost+found --timeout=900 --itemize-changes \
      /tmp/stage/ "tak-feed@$TAK_HOST:/"
    ;;
  *)
    echo "unknown mode: $mode" >&2
    exit 2
    ;;
esac
