#!/usr/bin/env bash
#
# Validates what Flux will actually apply, locally and in CI
# (.github/workflows/validate.yaml):
#
#   1. every YAML file parses
#   2. every path referenced by a Flux Kustomization in clusters/the-intersect
#      builds with kustomize
#   3. the built output passes kubeconform, using the Kubernetes schemas, the
#      Flux CRD schemas, and the datreeio CRD catalog for everything else
#
# Step 3 runs on the output after a stand-in for Flux's postBuild substitution:
# real ${DOMAIN_*}/${SUB_*}/IP values live only in the encrypted cluster-vars
# Secret, and the unsubstituted tokens fail hostname/IP schema patterns.
#
# Prerequisites: yq v4, kustomize v5, kubeconform v0.7, perl, curl.

set -o errexit
set -o nounset
set -o pipefail

cd "$(dirname "$0")/.."

cluster_dir=clusters/the-intersect
kubernetes_version=${KUBERNETES_VERSION:-1.33.0}
schema_dir=${TMPDIR:-/tmp}/flux-crd-schemas

# Secrets are skipped: their sops metadata fails strict validation.
kubeconform_flags=(
  -strict
  -summary
  -skip=Secret
  -ignore-missing-schemas
  -kubernetes-version "$kubernetes_version"
  -schema-location default
  -schema-location "$schema_dir"
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
)

# Stand-in for Flux postBuild substitution. Only ${UPPER_CASE} tokens are
# touched, matching what Flux substitutes from cluster-vars.
substitute() {
  perl -pe '
    s/\$\{(DOMAIN_[A-Z0-9_]+)\}/example.com/g;
    s/\$\{(SUB_[A-Z0-9_]+)\}/sub/g;
    s/\$\{([A-Z0-9_]*(?:_IP|_START|_STOP|_END|_GATEWAY))\}/192.0.2.10/g;
    s/\$\{([A-Z0-9_]*(?:_SUBNET|_RANGE))\}/192.0.2.0\/24/g;
    s/\$\{([A-Z][A-Z0-9_]*)\}/placeholder/g;
  '
}

echo "INFO - Downloading Flux OpenAPI schemas"
mkdir -p "$schema_dir/master-standalone-strict"
curl -sfL https://github.com/fluxcd/flux2/releases/latest/download/crd-schemas.tar.gz |
  tar zxf - -C "$schema_dir/master-standalone-strict"

echo "INFO - Parsing YAML"
find . -path ./.git -prune -o -path ./.claude -prune -o -type f -name '*.yaml' -print0 |
  while IFS= read -r -d $'\0' file; do
    yq e 'true' "$file" >/dev/null || { echo "ERROR - $file does not parse"; exit 1; }
  done

echo "INFO - Validating $cluster_dir"
for file in "$cluster_dir"/*.yaml; do
  kubeconform "${kubeconform_flags[@]}" "$file"
done

failed=0
paths=$(yq e -N 'select(.kind == "Kustomization" and .apiVersion == "kustomize.toolkit.fluxcd.io/*") | .spec.path' "$cluster_dir"/*.yaml | sort -u)
for path in $paths; do
  echo "INFO - Validating $path"
  if ! kustomize build --load-restrictor=LoadRestrictionsNone "$path" | substitute | kubeconform "${kubeconform_flags[@]}"; then
    echo "ERROR - $path failed validation"
    failed=1
  fi
done

exit "$failed"
