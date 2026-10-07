# Forgejo access

**Normal sign-in:** Pocket ID only (`gitea.oauth` in `values.yaml`). The account must be in the Pocket ID group `forgejo-users`, and `forgejo-admins` grants Forgejo admin. Admin is re-synced from the group on every SSO login. The very first login, which links an existing account, does not sync it, so sign in twice.

**Off:**
- password sign-in on the web (`ENABLE_INTERNAL_SIGNIN=false`);
- passwords over HTTP Basic auth (`ENABLE_BASIC_AUTHENTICATION=false`);
- registration (`DISABLE_REGISTRATION=true`);
- anonymous viewing (`REQUIRE_SIGNIN_VIEW=true`).

## Git, API and registry

- **Git:** use SSH keys, or an access token as the HTTPS password (Settings → Applications). SSO accounts have no password.
- **`docker login`:** your username plus an access token with the `package` scope.
- **CI:** Forgejo Actions uses its automatic job token.

## Break-glass

`intersect-admin` is a local admin whose password (rotated 2026-10-07, about 64 random characters) lives in `values-secret.sops.yaml`. The chart's `passwordMode: keepUpdated` re-applies it on every start. Password login is off, so the password alone gets nothing. Everything below needs cluster access.

```bash
fj() { kubectl -n forgejo exec deploy/forgejo -c forgejo -- forgejo "$@"; }

fj admin user list --admin               # who is admin
fj admin auth list                       # is the pocket-id source there?
fj admin user change-password --username <user> --password '<new>'   # local users only

# Admin API access without the web UI: a short-lived token, deleted afterwards
fj admin user generate-access-token --username intersect-admin \
   --token-name breakglass --scopes all --raw
# ...use it, then revoke it (Settings → Applications as that user, or):
kubectl -n forgejo exec forgejo-postgres-1 -c postgres -- \
  psql -U postgres -d forgejo -c "DELETE FROM access_token WHERE name='breakglass';"
```

### Pocket ID down

1. Open a PR setting `ENABLE_INTERNAL_SIGNIN: true` in `values.yaml`.
2. Sign in as `intersect-admin` with the password from
   `sops -d apps/forgejo/the-intersect/values-secret.sops.yaml`.
3. Revert the PR when Pocket ID is back.
