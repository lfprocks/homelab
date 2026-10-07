#!/usr/bin/env bash
# Pull-mirror GitHub repositories into Forgejo. GitHub stays the source of
# truth; Forgejo syncs every hour. Idempotent: existing repos are skipped, so
# re-run it to pick up new repositories (or private ones skipped earlier).
#
#   - one Forgejo org per GitHub org (created for $FORGEJO_USER, so you own it);
#     personal repos go to your Forgejo user
#   - forks are skipped; archived repos are mirrored and archived in Forgejo
#   - visibility is preserved; only PRIVATE repos store a GitHub token (it is
#     needed on every sync); public ones are fetched anonymously
#   - wikis are not mirrored (GitHub reports has_wiki for empty wikis, and a
#     missing wiki fails the migration)
#
# Credentials:
#   - repo LISTS come from your `gh` login (gh api); that token is never stored.
#   - PRIVATE repos need a credential Forgejo keeps for every sync. A GitHub
#     fine-grained token covers ONE resource owner, so the script asks for one
#     per owner that has private repos: Resource owner = that user/org,
#     All repositories, Contents: Read. Press Enter to skip that owner's
#     private repos (re-run later to add them).
#
# Usage: mirror-github.sh <github-user> [<org> ...]
# Prints no secrets. Needs gh (logged in) and kubectl access to forgejo.
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <github-user> [<org> ...]" >&2; exit 2; }
GH_USER=$1; shift
FORGEJO_USER=${FORGEJO_USER:-$GH_USER}
INTERVAL=1h

gh auth status >/dev/null 2>&1 || { echo "gh is not logged in" >&2; exit 1; }
GH_TOKEN=""

fj()  { kubectl -n forgejo exec deploy/forgejo -c forgejo -- forgejo "$@"; }
sql() { kubectl -n forgejo exec forgejo-postgres-1 -c postgres -- psql -U postgres -d forgejo -tAc "$1"; }
HOST=$(kubectl -n forgejo get httproute -o jsonpath='{.items[0].spec.hostnames[0]}')
ADMIN_TOKEN_NAME="mirror-github-$$"
BODY=$(mktemp); LIST=$(mktemp)
cleanup() {
  sql "DELETE FROM access_token WHERE name='${ADMIN_TOKEN_NAME}'" >/dev/null || true
  rm -f "$BODY" "$LIST"
  GH_TOKEN=""
}
trap cleanup EXIT
ADMIN_TOKEN=$(fj admin user generate-access-token --username intersect-admin \
  --token-name "$ADMIN_TOKEN_NAME" --scopes write:admin,write:organization,write:repository,write:user --raw 2>/dev/null | tail -1)

fapi() { # METHOD PATH [JSON] -> HTTP code; body in $BODY
  curl -s -m 1800 -o "$BODY" -w '%{http_code}' -X "$1" -H "Authorization: token $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' ${3:+-d "$3"} "https://$HOST/api/v1$2"
}

gh_list() { # owner kind(user|org) -> TSV in $LIST: name private archived description clone_url
  local url
  if [ "$2" = user ]; then url="user/repos?affiliation=owner&per_page=100"
  else url="orgs/$1/repos?type=all&per_page=100"; fi
  gh api --paginate "$url" --jq '.[] | select(.fork | not) |
    [.name, (.private|tostring), (.archived|tostring),
     ((.description // "") | gsub("[\\t\\n]"; " ")), .clone_url] | @tsv' > "$LIST" \
    || { echo "listing $1 failed" >&2; return 1; }
}

created=0 skipped=0 skipped_priv=0 failed=0
mirror_owner() { # gh_owner kind forgejo_owner
  gh_list "$1" "$2" || { failed=$((failed+1)); return; }
  GH_TOKEN=""
  npriv=$(awk -F'\t' '$2=="true"' "$LIST" | wc -l | tr -d ' ')
  if [ "$npriv" -gt 0 ]; then
    read -rsp "  $1 has $npriv private repo(s). Fine-grained read-only token for $1 (Enter = skip private): " GH_TOKEN; echo
  fi
  while IFS=$'\t' read -r name private archived desc url; do
    if [ "$private" = true ] && [ -z "$GH_TOKEN" ]; then skipped_priv=$((skipped_priv+1)); continue; fi
    if [ "$(fapi GET "/repos/$3/$name")" = 200 ]; then skipped=$((skipped+1)); continue; fi
    payload=$(NAME="$name" PRIV="$private" DESC="$desc" URL="$url" OWNER="$3" TOK="$GH_TOKEN" INT="$INTERVAL" python3 -c '
import json, os
p = {"clone_addr": os.environ["URL"], "repo_owner": os.environ["OWNER"], "repo_name": os.environ["NAME"],
     "service": "github", "mirror": True, "mirror_interval": os.environ["INT"],
     "private": os.environ["PRIV"] == "true", "description": os.environ["DESC"][:2048],
     "lfs": True, "wiki": False}
if p["private"]:
    p["auth_token"] = os.environ["TOK"]
print(json.dumps(p))')
    code=$(fapi POST /repos/migrate "$payload"); payload=""
    if [ "$code" = 201 ]; then
      created=$((created+1))
      if [ "$archived" = true ]; then fapi PATCH "/repos/$3/$name" '{"archived":true}' >/dev/null; fi
      printf '  + %s/%s%s\n' "$3" "$name" "$([ "$private" = true ] && echo ' (private)')"
    else
      failed=$((failed+1)); printf '  ! %s/%s: HTTP %s %s\n' "$3" "$name" "$code" "$(head -c 160 "$BODY")"
    fi
  done < "$LIST"
  GH_TOKEN=""
}

echo "== $GH_USER (personal) -> $FORGEJO_USER"
mirror_owner "$GH_USER" user "$FORGEJO_USER"
for org in "$@"; do
  forg=$(echo "$org" | tr '[:upper:]' '[:lower:]')
  if [ "$(fapi GET "/orgs/$forg")" != 200 ]; then
    code=$(fapi POST "/admin/users/$FORGEJO_USER/orgs" "{\"username\":\"$forg\",\"visibility\":\"public\"}")
    [ "$code" = 201 ] || { echo "create org $forg failed: HTTP $code $(head -c 160 "$BODY")" >&2; failed=$((failed+1)); continue; }
    echo "== created org $forg"
  fi
  echo "== $org -> $forg"
  mirror_owner "$org" org "$forg"
done
echo
echo "created $created, already present $skipped, private skipped (no token) $skipped_priv, failed $failed"
