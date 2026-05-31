#!/bin/bash

# Check if exactly one argument is provided
if [ "$#" -ne 1 ]; then
    echo "usage: $0 <path>"
    exit 1
fi

yq '.data = (.data | map_values(@base64))' -i $1
 
sops -e -i $1