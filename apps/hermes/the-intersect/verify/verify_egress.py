#!/usr/bin/env python3
"""Checks a pod's network policy from inside the pod.

Reads [{"name","host","port","expect":"allow"|"deny"}] on stdin and attempts a TCP
connection to each. Exits 0 if all match, 1 on any mismatch, 2 on bad input. Stdlib
only. Targets stay outside git, because this repo is public:

    kubectl -n hermes exec -i hermes -c hermes -- \
      python3 -I -c "$(cat verify_egress.py)" < targets.json
"""
import json
import socket
import sys

TIMEOUT_SECONDS = 5


def probe(host, port):
    """allow on connect, deny only on a timeout, which is how the policy's silent drop looks.

    A refusal proves the packet arrived, and a DNS or other error proves nothing, so
    neither may pass as denied.
    """
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_SECONDS):
            return "allow"
    except TimeoutError:
        return "deny"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "error"


def main():
    targets = json.load(sys.stdin)
    if not targets or any(t["expect"] not in ("allow", "deny") for t in targets):
        print("targets must be a non-empty list with expect set to allow or deny", file=sys.stderr)
        return 2

    mismatches = 0
    for target in targets:
        result = probe(target["host"], target["port"])
        matched = result == target["expect"]
        mismatches += not matched
        status = "ok" if matched else "MISMATCH"
        print(f"{status:9} {target['name']:32} expected={target['expect']} got={result}", flush=True)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
