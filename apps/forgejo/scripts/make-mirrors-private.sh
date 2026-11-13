#!/usr/bin/env bash
# Mark every pull mirror on Forgejo private, so anonymous visitors see only
# native (non-mirror) repos. Mirrors keep syncing; signed-in owners still see
# them. Non-mirror repos are never touched.
#
# Usage: make-mirrors-private.sh           # dry run: counts and lists, changes nothing
#        make-mirrors-private.sh --apply   # PATCH each public mirror to private
#
# Same credential pattern as mirror-github.sh: a temporary intersect-admin token
# minted in the pod, used only by curl inside the pod, deleted on exit. Prints
# no secrets. Needs kubectl access to the forgejo namespace.
set -euo pipefail
APPLY=false
[ "${1:-}" = "--apply" ] && APPLY=true

fj()  { kubectl -n forgejo exec deploy/forgejo -c forgejo -- forgejo "$@"; }
sql() { kubectl -n forgejo exec forgejo-postgres-1 -c postgres -- psql -U postgres -d forgejo -tAc "$1"; }
ADMIN_TOKEN_NAME="mirrors-private-$$"
LIST=$(mktemp)
cleanup() { sql "DELETE FROM access_token WHERE name='${ADMIN_TOKEN_NAME}'" >/dev/null || true; rm -f "$LIST"; }
trap cleanup EXIT

fapi() { # METHOD PATH [JSON] -> prints body then HTTP code on the last line
  printf '%s' "${3:-}" | kubectl -n forgejo exec -i deploy/forgejo -c forgejo -- \
    curl -s -m 120 -w '\n%{http_code}' -X "$1" -H "Authorization: token $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' ${3:+--data-binary @-} "http://localhost:3000/api/v1$2"
}

# Public mirrors, from the database (the source of truth for is_mirror/is_private).
sql "SELECT owner_name || '/' || name FROM repository WHERE is_mirror AND NOT is_private ORDER BY 1" | sed '/^$/d' > "$LIST"
count=$(wc -l < "$LIST" | tr -d ' ')
total=$(sql "SELECT count(*) FROM repository WHERE is_mirror")
native_public=$(sql "SELECT count(*) FROM repository WHERE NOT is_mirror AND NOT is_private")
echo "mirrors: $total total, $count public; native public repos (left alone): $native_public"

if ! $APPLY; then
  head -20 "$LIST" | sed 's/^/  would make private: /'
  [ "$count" -gt 20 ] && echo "  ... and $((count - 20)) more"
  echo "dry run; re-run with --apply"
  exit 0
fi

ADMIN_TOKEN=$(fj admin user generate-access-token --username intersect-admin \
  --token-name "$ADMIN_TOKEN_NAME" --scopes write:admin,write:repository --raw 2>/dev/null | tail -1)
ok=0; failed=0
while IFS= read -r full; do
  code=$(fapi PATCH "/repos/$full" '{"private":true}' | tail -1)
  if [ "$code" = 200 ]; then ok=$((ok+1)); else failed=$((failed+1)); echo "  FAILED ($code): $full"; fi
done < "$LIST"
echo "made private: $ok, failed: $failed"
echo "public mirrors left: $(sql "SELECT count(*) FROM repository WHERE is_mirror AND NOT is_private")"
