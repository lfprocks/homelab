"""Push the cert-manager certificate for an iDRAC9 into the iDRAC over Redfish.

Compares the SHA-256 fingerprint of the leaf certificate in the mounted secret
with the one the iDRAC serves on 443. If they differ, uploads the private key
(DelliDRACCardService.UploadSSLKey) and then the certificate chain
(DelliDRACCardService.ImportSSLCertificate, type Server), Dell's import path
for an externally generated key. The web server then restarts on its own; if
it is still serving the old certificate after a few minutes, the script
restarts the iDRAC. Idempotent.

iDRAC7 (olympic) has no such Redfish action and uses push_cert.sh with racadm.

API calls carry the iDRAC password, so they verify TLS against IDRAC_HOST
(which must be the certificate's name). Renewals happen while the previous
Let's Encrypt certificate is still valid, so verification holds from then on.
Only the very first push, when the iDRAC still has Dell's self-signed
certificate, needs BOOTSTRAP_INSECURE=true; set it on a one-off Job, never on
the CronJob. Reading the served fingerprint sends no credentials and does not
verify.

Env: IDRAC_HOST, IDRAC_USER, IDRAC_PASSWORD, optional BOOTSTRAP_INSECURE.
Files: /tls/tls.crt, /tls/tls.key.
"""

import base64
import hashlib
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request

HOST = os.environ["IDRAC_HOST"]
USER = os.environ["IDRAC_USER"]
PASSWORD = os.environ["IDRAC_PASSWORD"]
CARD = "/redfish/v1/Managers/iDRAC.Embedded.1/Oem/Dell/DelliDRACCardService/Actions"
WAIT_SECONDS = 300

insecure = ssl.create_default_context()
insecure.check_hostname = False
insecure.verify_mode = ssl.CERT_NONE
if os.environ.get("BOOTSTRAP_INSECURE") == "true":
    print("BOOTSTRAP_INSECURE: not verifying the iDRAC's certificate")
    api_context = insecure
else:
    api_context = ssl.create_default_context()


def served_fingerprint():
    try:
        with socket.create_connection((HOST, 443), timeout=10) as sock:
            with insecure.wrap_socket(sock, server_hostname=HOST) as tls:
                return hashlib.sha256(tls.getpeercert(binary_form=True)).hexdigest()
    except OSError:
        return None  # restarting


def redfish(path, body=None):
    auth = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"https://{HOST}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, context=api_context, timeout=60) as resp:
            print(f"  {path.rsplit('/', 1)[-1]}: HTTP {resp.status}")
    except urllib.error.HTTPError as err:
        sys.exit(f"{path} failed: HTTP {err.code} {err.read().decode(errors='replace')}")


chain = open("/tls/tls.crt").read()
key = open("/tls/tls.key").read()
leaf = chain[: chain.index("-----END CERTIFICATE-----") + len("-----END CERTIFICATE-----")]
want = hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf)).hexdigest()
have = served_fingerprint()
print(f"secret cert {want[:16]}  iDRAC serves {(have or 'nothing')[:16]}")
if want == have:
    print("iDRAC already serves the current certificate; nothing to do")
    sys.exit(0)

print("uploading private key and certificate chain")
redfish(f"{CARD}/DelliDRACCardService.UploadSSLKey", {"SSLKeyString": key})
redfish(
    f"{CARD}/DelliDRACCardService.ImportSSLCertificate",
    {"CertificateType": "Server", "SSLCertificateFile": chain},
)


def wait_for_new_cert():
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        time.sleep(15)
        if served_fingerprint() == want:
            return True
    return False


print("waiting for the web server to pick it up")
if wait_for_new_cert():
    print("iDRAC now serves the new certificate")
    sys.exit(0)

print("still serving the old certificate; restarting the iDRAC")
redfish(
    "/redfish/v1/Managers/iDRAC.Embedded.1/Actions/Manager.Reset",
    {"ResetType": "GracefulRestart"},
)
if not wait_for_new_cert():
    sys.exit("iDRAC is not serving the new certificate after a restart")
print("iDRAC now serves the new certificate")
