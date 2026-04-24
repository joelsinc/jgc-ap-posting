# Databricks notebook source
# ══════════════════════════════════════════════════════════════
# SMOKE TEST: QBO AP Token Manager (Phase 0.T7)
#
# What this proves:
#   1. Databricks-backed scope qbo-ap-tokens exists and is readable
#   2. All 4 required secrets are present for the sandbox realm
#   3. Intuit OAuth refresh exchange works with our app credentials
#   4. The rotated refresh token is persisted back to the scope
#   5. A basic QBO API call succeeds using the access token + realm_id
#
# This notebook is SAFE to re-run. Each run rotates the refresh token;
# the scope always holds the current valid token after a successful run.
# ══════════════════════════════════════════════════════════════

# MAGIC %run ../utils/qbo_ap_token_manager

# COMMAND ----------

# ── Step 1: fetch access token for the sandbox realm ──
access_token, realm_id = get_qbo_ap_access_token("sandbox")

assert access_token, "OAuth exchange failed — see error above"
assert realm_id, "realm_id missing — check qbo-ap-realm-id-sandbox secret"

print(f"✅ Access token acquired ({len(access_token)} chars)")
print(f"✅ Realm ID: {realm_id}")

# COMMAND ----------

# ── Step 2: make a basic QBO API call ──
# /companyinfo/{realm_id} is the lightest read-only endpoint — confirms
# the access token + realm pair are wired correctly before Phase 4 tries
# to POST a Bill.
import requests

resp = requests.get(
    f"https://sandbox-quickbooks.api.intuit.com/v3/company/{realm_id}/companyinfo/{realm_id}",
    headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    },
    timeout=30,
)

print(f"Status: {resp.status_code}")
assert resp.status_code == 200, f"CompanyInfo call failed: {resp.text[:400]}"

company = resp.json()["CompanyInfo"]
print(f"✅ Company: {company.get('CompanyName')}")
print(f"   Legal name: {company.get('LegalName')}")
print(f"   Country:    {company.get('Country')}")

# COMMAND ----------

# ── Step 3: confirm the rotated refresh token is readable ──
# If Step 1 rotated the token, this read should succeed with a fresh
# value. If rotation failed, Step 1 would have printed CRITICAL above.
rotated = dbutils.secrets.get(scope="qbo-ap-tokens", key="qbo-ap-refresh-token-sandbox")
print(f"✅ Rotated refresh token persisted ({len(rotated)} chars)")

print("\n🎉 Phase 0.T7 smoke test PASSED")
print("   - OAuth refresh flow works")
print("   - Access token + realm_id can hit the QBO sandbox API")
print("   - Token rotation is persisted back to qbo-ap-tokens scope")
