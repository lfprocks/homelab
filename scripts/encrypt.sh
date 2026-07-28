#!/bin/bash
#
# Base64-encodes a Secret's `data` values, then encrypts the file with SOPS.
#
set -euo pipefail

# Check if exactly one argument is provided
if [ "$#" -ne 1 ]; then
    echo "usage: $0 <path>"
    exit 1
fi

# Only touch `data` if the file actually has one.
#
# Kubernetes Secrets take either `data` (base64) or `stringData` (plain), and
# every secret in this repository uses `stringData` -- which needs no encoding,
# because the API server does it. Assigning `.data` unconditionally is not a
# no-op on those files: yq evaluates `null | map_values(...)` to an empty array
# and writes out `data: []`, which the API server then rejects with
#
#   cannot unmarshal array into Go struct field Secret.data of type
#   map[string][]uint8
#
# SOPS encrypts that broken file quite happily, so the damage only surfaces
# later as a Flux apply failure.
if [ "$(yq 'has("data")' "$1")" = "true" ]; then
    yq '.data = (.data | map_values(@base64))' -i "$1"
fi

sops -e -i "$1"
