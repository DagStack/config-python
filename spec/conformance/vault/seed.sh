#!/usr/bin/env bash
# Seed the dev-mode Vault with the conformance KV v2 payloads referenced
# by the `phase2_secrets_vault`-tagged fixtures.
#
# Pre-requisites:
#   - `docker compose -f conformance/vault/docker-compose.yml up -d` (or
#     equivalent reachable Vault).
#   - DAGSTACK_CONFORMANCE_VAULT_ADDR — base URL (e.g. http://localhost:8200).
#   - DAGSTACK_CONFORMANCE_VAULT_TOKEN — root token for the dev mode
#     (matches `VAULT_DEV_ROOT_TOKEN_ID` in docker-compose.yml).
#   - `vault` CLI on PATH OR `curl` + `jq`. This script uses only `curl`
#     to keep the dependency surface tight.
#
# Idempotent: re-running overwrites the seed payloads in place.

set -euo pipefail

: "${DAGSTACK_CONFORMANCE_VAULT_ADDR:?env var required}"
: "${DAGSTACK_CONFORMANCE_VAULT_TOKEN:?env var required}"

addr="${DAGSTACK_CONFORMANCE_VAULT_ADDR%/}"
hdr="X-Vault-Token: ${DAGSTACK_CONFORMANCE_VAULT_TOKEN}"

put_secret() {
    local path="$1"
    local payload="$2"
    curl -sS -fL \
        -H "${hdr}" \
        -H "Content-Type: application/json" \
        -X POST \
        -d "${payload}" \
        "${addr}/v1/secret/data/${path}" >/dev/null
    echo "  ✓ secret/${path}"
}

echo "Seeding Vault at ${addr} ..."

# Single-key envelope — used by the simple-resolve fixture.
put_secret "dagstack/conformance/openai" '{"data":{"value":"sk-conformance-fixed"}}'

# Multi-key envelope — used by the #field projection fixture.
put_secret "dagstack/conformance/db" '{"data":{"username":"conformance-user","password":"conformance-pw"}}'

# Multi-key envelope (no #field) — used by the §1.2 normative-error fixture.
put_secret "dagstack/conformance/multi" '{"data":{"alpha":"a","beta":"b"}}'

# Versioned secret — write twice to materialise version 2.
put_secret "dagstack/conformance/versioned" '{"data":{"value":"v1"}}'
put_secret "dagstack/conformance/versioned" '{"data":{"value":"v2"}}'

echo "Done. Run binding-side integration suite with the fixtures under"
echo "phase2_secrets_vault tag (per _meta/conformance_tags.yaml)."
