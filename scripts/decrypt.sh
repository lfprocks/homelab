#!/bin/bash

# Check if exactly one argument is provided
if [ "$#" -ne 1 ]; then
    echo "usage: $0 <path>"
    exit 1
fi

# Temp file to hold the output before overwriting
TMP_FILE=$(mktemp)

# Decrypt, modify, and write to temp file
SOPS_AGE_KEY_FILE=age.agekey sops -d "$1" | yq e '.data = (.data | map_values(@base64d))' | tee $TMP_FILE

# Overwrite the original file with the new content
mv "$TMP_FILE" "$1"