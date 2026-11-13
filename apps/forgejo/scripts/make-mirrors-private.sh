#!/usr/bin/env bash
# Mark every pull mirror on Forgejo private, and every org that holds only
# mirrors 'limited' (visible to signed-in users only), so anonymous visitors see
# only native repos and any image ever published under a mirror org stays
# behind sign-in (packages follow their owner's visibility). Mirrors keep
# syncing; signed-in owners still see them. Non-mirror repos, users, and orgs
# that own a native repo are never touched.
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
LIST=$(mktemp); ORGS=$(mktemp)
cleanup() { sql "DELETE FROM access_token WHERE name='${ADMIN_TOKEN_NAME}'" >/dev/null || true; rm -f "$LIST" "$ORGS"; }
trap cleanup EXIT

fapi() { # METHOD PATH [JSON] -> prints body then HTTP code on the last line
  printf '%s' "${3:-}" | kubectl -n forgejo exec -i deploy/forgejo -c forgejo -- \
    curl -s -m 120 -w '\n%{http_code}' -X "$1" -H "Authorization: token $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' ${3:+--data-binary @-} "http://localhost:3000/api/v1$2"
}

# Public mirrors, from the database (the source of truth for is_mirror/is_private).
sql "SELECT owner_name || '/' || name FROM repository WHERE is_mirror AND NOT is_private ORDER BY 1" | sed '/^$/d' > "$LIST"
count=$(wc -l < "$LIST" | tr -d ' ')
# Public orgs (type 1) with at least one repo, all of them mirrors.
sql "SELECT u.name FROM \"user\" u JOIN repository r ON r.owner_id=u.id WHERE u.type=1 AND u.visibility=0 GROUP BY u.name HAVING bool_and(r.is_mirror) ORDER BY 1" | sed '/^$/d' > "$ORGS"
norgs=$(wc -l < "$ORGS" | tr -d ' ')
total=$(sql "SELECT count(*) FROM repository WHERE is_mirror")
native_public=$(sql "SELECT count(*) FROM repository WHERE NOT is_mirror AND NOT is_private")
echo "mirrors: $total total, $count public; native public repos (left alone): $native_public"
echo "public mirror-only orgs: $norgs"

if ! $APPLY; then
  head -20 "$LIST" | sed 's/^/  would make private: /'
  [ "$count" -gt 20 ] && echo "  ... and $((count - 20)) more"
  sed 's/^/  would make org limited: /' "$ORGS"
  echo "dry run; re-run with --apply"
  exit 0
fi

ADMIN_TOKEN=$(fj admin user generate-access-token --username intersect-admin \
  --token-name "$ADMIN_TOKEN_NAME" --scopes write:admin,write:repository,write:organization --raw 2>/dev/null | tail -1)
ok=0; failed=0
while IFS= read -r full; do
  code=$(fapi PATCH "/repos/$full" '{"private":true}' | tail -1)
  if [ "$code" = 200 ]; then ok=$((ok+1)); else failed=$((failed+1)); echo "  FAILED ($code): $full"; fi
done < "$LIST"
echo "made private: $ok, failed: $failed"

orgs_ok=0
while IFS= read -r org; do
  code=$(fapi PATCH "/orgs/$org" '{"visibility":"limited"}' | tail -1)
  if [ "$code" = 200 ]; then orgs_ok=$((orgs_ok+1)); else failed=$((failed+1)); echo "  FAILED ($code): org $org"; fi
done < "$ORGS"
echo "orgs made limited: $orgs_ok"
echo "public mirrors left: $(sql "SELECT count(*) FROM repository WHERE is_mirror AND NOT is_private")"
