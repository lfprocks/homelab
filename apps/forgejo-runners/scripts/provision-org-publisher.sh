#!/usr/bin/env bash
# Give a Forgejo organization the ability to publish images from CI.
#
# Forgejo's automatic job token cannot write packages (tested on 15.0.9, even
# with `permissions: packages: write`), so workflows push with a token of the
# bot user ci-publisher instead. Per org, idempotently:
#   - ensure bot user ci-publisher (no usable password; login is SSO-only)
#   - ensure team `ci-publishers` (packages: write, code: read), bot is member
#   - mint a write:package token for the bot, store it as the org's Actions
#     secret REGISTRY_TOKEN, and set variable REGISTRY_USER=ci-publisher
#     (one token per org: revoke one org without touching the others;
#     re-running rotates that org's token)
#
# Workflows then: echo "${{ secrets.REGISTRY_TOKEN }}" | docker login <host> \
#   -u "${{ vars.REGISTRY_USER }}" --password-stdin
#
# Usage: provision-org-publisher.sh <org> [<org> ...]
# Needs kubectl access to the forgejo namespace. Uses a short-lived admin
# token (deleted on exit); prints no secrets.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <org> [<org> ...]" >&2; exit 2; }

BOT=ci-publisher
fj()  { kubectl -n forgejo exec deploy/forgejo -c forgejo -- forgejo "$@"; }
sql() { kubectl -n forgejo exec forgejo-postgres-1 -c postgres -- psql -U postgres -d forgejo -tAc "$1"; }
HOST=$(kubectl -n forgejo get httproute -o jsonpath='{.items[0].spec.hostnames[0]}')
ADMIN_TOKEN_NAME="provision-publisher-$$"

# api() runs inside $(...) subshells, so it cannot hand back a variable: the
# response body goes to this one file, created here in the parent shell.
BODY=$(mktemp)
cleanup() { sql "DELETE FROM access_token WHERE name='${ADMIN_TOKEN_NAME}'" >/dev/null || true; rm -f "$BODY"; }
trap cleanup EXIT
ADMIN_TOKEN=$(fj admin user generate-access-token --username intersect-admin \
  --token-name "$ADMIN_TOKEN_NAME" --scopes write:admin,write:organization,write:user --raw 2>/dev/null | tail -1)

api() { # api METHOD PATH [JSON] -> prints HTTP code; body in $BODY
  local m=$1 p=$2 d=${3:-}
  curl -s -m 30 -o "$BODY" -w '%{http_code}' -X "$m" \
    -H "Authorization: token $ADMIN_TOKEN" -H 'Content-Type: application/json' \
    ${d:+-d "$d"} "https://$HOST/api/v1$p"
}

if ! fj admin user list 2>/dev/null | awk '{print $2}' | grep -qx "$BOT"; then
  fj admin user create --username "$BOT" --email "$BOT@noreply.${HOST#*.}" \
    --random-password --must-change-password=false >/dev/null 2>&1
  echo "created bot user $BOT"
fi

for ORG in "$@"; do
  code=$(api GET "/orgs/$ORG"); [ "$code" = 200 ] || { echo "$ORG: org not found (HTTP $code)" >&2; exit 1; }

  team_id=$(api GET "/orgs/$ORG/teams/search?q=ci-publishers" >/dev/null; python3 -c '
import json,sys; d=json.load(open(sys.argv[1])); ts=d.get("data",d) if isinstance(d,dict) else d
print(next((t["id"] for t in ts if t["name"]=="ci-publishers"),""))' "$BODY")
  if [ -z "$team_id" ]; then
    code=$(api POST "/orgs/$ORG/teams" '{"name":"ci-publishers","description":"CI image publishing (bot)","permission":"read","includes_all_repositories":true,"units_map":{"repo.code":"read","repo.packages":"write"}}')
    [ "$code" = 201 ] || { echo "$ORG: create team failed (HTTP $code): $(head -c 200 "$BODY")" >&2; exit 1; }
    team_id=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["id"])' "$BODY")
  fi
  code=$(api PUT "/teams/$team_id/members/$BOT"); [ "$code" = 204 ] || { echo "$ORG: add member failed (HTTP $code)" >&2; exit 1; }

  tname="registry-$ORG"
  sql "DELETE FROM access_token WHERE name='${tname}' AND uid=(SELECT id FROM \"user\" WHERE lower_name='${BOT}')" >/dev/null
  tok=$(fj admin user generate-access-token --username "$BOT" --token-name "$tname" --scopes write:package,read:package --raw 2>/dev/null | tail -1)
  code=$(api PUT "/orgs/$ORG/actions/secrets/REGISTRY_TOKEN" "{\"data\":\"$tok\"}"); unset tok
  case "$code" in 201|204) ;; *) echo "$ORG: set secret failed (HTTP $code)" >&2; exit 1;; esac
  code=$(api POST "/orgs/$ORG/actions/variables/REGISTRY_USER" "{\"value\":\"$BOT\"}")
  [ "$code" = 201 ] || code=$(api PUT "/orgs/$ORG/actions/variables/REGISTRY_USER" "{\"value\":\"$BOT\"}")
  case "$code" in 201|204) ;; *) echo "$ORG: set variable failed (HTTP $code)" >&2; exit 1;; esac
  echo "$ORG: team ci-publishers (id $team_id), secret REGISTRY_TOKEN, variable REGISTRY_USER=$BOT"
done
