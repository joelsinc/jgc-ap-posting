# Databricks notebook source
# ══════════════════════════════════════════════════════════════
# UTILITY: QBO AP Token Manager — Production-Grade Token Rotation
#
# Mirrors the data platform's qbo_token_manager.py pattern but for the
# SEPARATE "JGC-AP-Pipeline" Intuit app used by the AP posting pipeline.
# Separate app = separate blast radius: rotating AP creds doesn't touch
# the GL extract's refresh token, and Intuit throttling on AP posts
# doesn't affect GL reads.
#
# Architecture:
#   - All secrets live in ONE Databricks-backed scope: qbo-ap-tokens
#     (writable, so the rotated refresh token can be persisted).
#   - qbo-ap-client-id-{env}, qbo-ap-client-secret-{env}  (env: dev|prod)
#   - qbo-ap-refresh-token-{realm_key}  (realm_key: sandbox|e001)
#   - qbo-ap-realm-id-{realm_key}
#
# Env-for-realm mapping (Intuit pairs Dev keys → sandbox, Prod keys → E001):
#   sandbox → dev keys
#   e001    → prod keys
#
# Token rotation: Databricks REST API writes the new refresh token back
# to the same scope on each call. If the write fails, the token is still
# usable in-memory for this run but the NEXT run will fail — the log
# message flags this as CRITICAL so it's caught immediately.
# ══════════════════════════════════════════════════════════════

import requests
from pyspark.sql import SparkSession

try:
    spark
except NameError:
    spark = SparkSession.builder.getOrCreate()

try:
    dbutils
except NameError:
    from pyspark.dbutils import DBUtils
    dbutils = DBUtils(spark)

# Single scope holds both read-only genesis values (client id/secret/
# realm id) and the writable rotated refresh token. All AP secrets live
# here so there's no KV↔Databricks wiring to maintain for the AP side.
SCOPE = "qbo-ap-tokens"

TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"

# Intuit pairs Development keys with the sandbox realm, Production keys
# with the live company realm. Realm → env is deterministic.
ENV_FOR_REALM = {
    "sandbox": "dev",
    "e001":    "prod",
}

# Module-level caches (one per worker). Client creds + workspace URL
# don't change between calls, so pay for the secrets lookup once.
_client_id = {}          # keyed by env
_client_secret = {}      # keyed by env
_api_token = None
_workspace_url = None


def _get_client_creds(env: str):
    """Read client_id + client_secret for the given env (dev|prod)."""
    if env not in _client_id:
        _client_id[env] = dbutils.secrets.get(
            scope=SCOPE, key=f"qbo-ap-client-id-{env}"
        )
        _client_secret[env] = dbutils.secrets.get(
            scope=SCOPE, key=f"qbo-ap-client-secret-{env}"
        )
    return _client_id[env], _client_secret[env]


def _get_api_context():
    """Databricks API token + workspace URL — for writing the rotated refresh token."""
    global _api_token, _workspace_url
    if _api_token is None:
        _api_token = (
            dbutils.notebook.entry_point
            .getDbutils().notebook().getContext()
            .apiToken().getOrElse(None)
        )
        _workspace_url = spark.conf.get("spark.databricks.workspaceUrl", "")
    return _api_token, _workspace_url


def _write_refresh_token(key: str, value: str) -> bool:
    """Write rotated refresh token to the same scope via Databricks REST API.

    Works only for Databricks-backed scopes (qbo-ap-tokens is one).
    Returns True on success, False otherwise. Caller should treat False
    as CRITICAL — the next run will fail to authenticate.
    """
    api_token, workspace_url = _get_api_context()
    if not api_token or not workspace_url:
        print(f"  ⚠️  No API token / workspace URL — cannot rotate {key}")
        return False

    try:
        resp = requests.post(
            f"https://{workspace_url}/api/2.0/secrets/put",
            headers={"Authorization": f"Bearer {api_token}"},
            json={"scope": SCOPE, "key": key, "string_value": value},
            timeout=30,
        )
        if resp.status_code == 200:
            print(f"  🔄 Refresh token rotated: {key}")
            return True
        print(f"  ❌ Rotate-write failed for {key}: {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as ex:
        print(f"  ❌ Rotate-write error for {key}: {str(ex)[:200]}")
        return False


def get_qbo_ap_access_token(realm_key: str):
    """Get an AP access token for the given realm, rotating the refresh token.

    Parameters
    ----------
    realm_key : str
        One of "sandbox" or "e001". Selects which refresh token + realm
        to use; the env (dev/prod client creds) is derived automatically.

    Returns
    -------
    (access_token, realm_id) : tuple[str, str] | tuple[None, None]
        Access token is valid ~1 hour. realm_id is needed by every
        QBO API call. Returns (None, None) on failure (already logged).

    Side effect
    -----------
    On success, writes the newly-rotated refresh token to the
    qbo-ap-tokens scope so the next call continues to work. Intuit
    invalidates the prior refresh token as soon as this call succeeds —
    failure to persist the new one breaks the next run.
    """
    env = ENV_FOR_REALM.get(realm_key)
    if env is None:
        print(f"  ❌ Unknown realm_key: {realm_key!r} (expected: sandbox | e001)")
        return None, None

    refresh_key = f"qbo-ap-refresh-token-{realm_key}"
    realm_secret = f"qbo-ap-realm-id-{realm_key}"

    client_id, client_secret = _get_client_creds(env)

    try:
        refresh_token = dbutils.secrets.get(scope=SCOPE, key=refresh_key)
        realm_id = dbutils.secrets.get(scope=SCOPE, key=realm_secret)
    except Exception as e:
        print(f"  ❌ Missing secret in {SCOPE}: {e}")
        return None, None

    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(client_id, client_secret),
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"  ❌ OAuth failed ({realm_key}): {resp.status_code} {resp.text[:200]}")
        return None, None

    payload = resp.json()
    access_token = payload["access_token"]
    new_refresh_token = payload.get("refresh_token", refresh_token)

    # CRITICAL: Intuit invalidates the old refresh token the instant
    # this call returns 200. Persist the new one immediately.
    if new_refresh_token and new_refresh_token != refresh_token:
        ok = _write_refresh_token(refresh_key, new_refresh_token)
        if not ok:
            print(
                f"  🚨 CRITICAL: {realm_key} refresh token rotated by Intuit "
                f"but NOT persisted. Next run will fail. Re-seed {refresh_key} "
                f"via the bootstrap-secrets GitHub Action."
            )

    return access_token, realm_id


print("✅ QBO AP Token Manager loaded")
print(f"   Scope: {SCOPE} (Databricks-backed, writable)")
print(f"   Supported realms: {list(ENV_FOR_REALM.keys())}")

# COMMAND ----------
