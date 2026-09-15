#!/usr/bin/env python3
"""Push the cert-manager certificate for olympic's iDRAC into the iDRAC.

Compares the SHA-256 fingerprint of the leaf certificate in the mounted
secret with the one the iDRAC is currently serving. If they differ, imports
certificate + key over Redfish (DelliDRACCardService.ImportSSLCertificate)
and issues a graceful iDRAC reset, which iDRAC7/8 need to start serving the
new certificate. Idempotent: re-runs are no-ops once the iDRAC serves it.

Env: IDRAC_HOST, IDRAC_USER, IDRAC_PASSWORD. Files: /tls/tls.crt, /tls/tls.key.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.request

TLS_DIR = "/tls"
IMPORT_ACTION = "/redfish/v1/Dell/Managers/iDRAC.Embedded.1/DelliDRACCardService/Actions/DelliDRACCardService.ImportSSLCertificate"
RESET_ACTION = "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Manager.Reset"
TIMEOUT = 30


def leaf_pem(chain_pem: str) -> str:
    end = "-----END CERTIFICATE-----"
    return chain_pem.split(end, 1)[0] + end + "\n"


def fingerprint(pem: str) -> str:
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def served_fingerprint(host: str) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()


def redfish_post(host: str, user: str, password: str, path: str, body: dict) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(
        f"https://{host}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            print(f"{path}: HTTP {resp.status}")
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")[:1000]
        sys.exit(f"{path}: HTTP {err.code}: {detail}")


def main() -> None:
    host = os.environ["IDRAC_HOST"]
    user = os.environ["IDRAC_USER"]
    password = os.environ["IDRAC_PASSWORD"]
    if password == "REPLACE_ME":
        sys.exit("IDRAC_PASSWORD is still the placeholder; set it in idrac-secret.yaml")

    with open(f"{TLS_DIR}/tls.crt") as f:
        chain = f.read()
    with open(f"{TLS_DIR}/tls.key") as f:
        key = f.read()

    want = fingerprint(leaf_pem(chain))
    have = served_fingerprint(host)
    print(f"secret cert sha256={want[:16]}  iDRAC serves sha256={have[:16]}")
    if want == have:
        print("iDRAC already serves the current certificate; nothing to do")
        return

    print("importing certificate + key into the iDRAC")
    redfish_post(host, user, password, IMPORT_ACTION, {
        "CertificateType": "Server",
        "SSLCertificateFile": chain + key,
    })
    print("resetting the iDRAC so it starts serving the new certificate")
    redfish_post(host, user, password, RESET_ACTION, {"ResetType": "GracefulRestart"})
    print("done; the iDRAC will be unreachable for a minute or two")


if __name__ == "__main__":
    main()
