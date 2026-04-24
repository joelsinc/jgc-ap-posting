# JGC AP Posting Pipeline

Databricks Asset Bundle that posts approved invoice captures from the JGC AP pipeline
as Bills into QuickBooks Online for entity E001. Companion repo to
[`jgc-invoice-automation`](https://github.com/joelsinc/jgc-invoice-automation)
(invoice capture side) and [`jgc-data-platform`](https://github.com/joelsinc/jgc-data-platform)
(consolidated reporting).

## Architecture

```
SharePoint Reports/*.xlsx
       │  (Phase 2 — Azure Function)
       ▼
ADLS raw/invoice_captures/YYYY-MM/*.json
       │  (Phase 3 — Databricks Auto Loader)
       ▼
jgc.silver.fact_invoice_captures
       │  (Phase 4 — THIS REPO)
       ▼
QBO Bills  (entity E001)
```

Scope is E001 only — permanent, not an MVP narrowing. Other entities are out of scope
for the AP pipeline and handled through the broader data platform's GL extract.

## Repository Structure

```
jgc-ap-posting/
├── databricks.yml                     # Asset Bundle config
├── databricks/
│   ├── utils/
│   │   └── qbo_ap_token_manager.py    # OAuth refresh + auto-rotation
│   ├── notebooks/
│   │   └── test_ap_token.py           # Phase 0.T7 smoke test
│   └── config/                         # Class IDs, category→account CSVs (Phase 0.T4/T5)
├── .github/workflows/
│   ├── deploy-to-databricks.yml       # Syncs repo → Databricks workspace on push
│   ├── deploy-jobs.yml                # Deploys Asset Bundle on databricks.yml change
│   └── bootstrap-secrets.yml          # One-shot manual: seeds Databricks scope from GH secrets
└── README.md
```

## One-time bootstrap

### 1. Connect the repo to Databricks (one-time, UI action)

In Databricks workspace → **Repos** → **Add Repo**:
- URL: `https://github.com/joelsinc/jgc-ap-posting`
- Branch: `main`

After this, the `deploy-to-databricks.yml` workflow can sync pushes into the workspace.

### 2. Add GitHub repo secrets

Repo → **Settings** → **Secrets and variables** → **Actions**. Add:

| Secret | Value | When |
|---|---|---|
| `DATABRICKS_HOST` | `https://adb-7405612201335882.2.azuredatabricks.net` | Bootstrap |
| `DATABRICKS_TOKEN` | Databricks personal access token (admin) | Bootstrap |
| `AP_QBO_CLIENT_ID_DEV` | Intuit Dev app Client ID | Before sandbox bootstrap |
| `AP_QBO_CLIENT_SECRET_DEV` | Intuit Dev app Client Secret | Before sandbox bootstrap |
| `AP_QBO_REFRESH_TOKEN_SANDBOX` | Initial refresh token from OAuth Playground | Before sandbox bootstrap |
| `AP_QBO_REALM_ID_SANDBOX` | Sandbox company Realm ID | Before sandbox bootstrap |
| `AP_QBO_CLIENT_ID_PROD` | Intuit Prod app Client ID | Before E001 cutover |
| `AP_QBO_CLIENT_SECRET_PROD` | Intuit Prod app Client Secret | Before E001 cutover |
| `AP_QBO_REFRESH_TOKEN_E001` | Initial refresh token from OAuth Playground | Before E001 cutover |
| `AP_QBO_REALM_ID_E001` | E001 Realm ID | Before E001 cutover |

### 3. Seed the Databricks scope

Actions → **Bootstrap AP Secrets → Databricks Scope** → **Run workflow**.

Choose `realm_key = sandbox` (first run) or `realm_key = e001` (production cutover).

The workflow creates the `qbo-ap-tokens` scope if missing and writes the four
secrets for the chosen realm. Safe to re-run — each run overwrites.

**After a successful bootstrap, remove or rotate the GitHub repo secrets.** The
Databricks scope becomes the source of truth; the refresh token rotates inside
Databricks on every use and is never written back to GitHub.

### 4. Smoke-test

Open `databricks/notebooks/test_ap_token.py` in the Databricks workspace and run.
Expected output:
- ✅ Access token acquired
- ✅ Realm ID resolved
- ✅ `/companyinfo` returns the sandbox company name
- ✅ Rotated refresh token persisted

## Deployment

### CI/CD

Push to `main` → two GitHub Actions workflows fire in parallel:
- `deploy-to-databricks.yml` — syncs the repo branch in Databricks Repos
- `deploy-jobs.yml` — runs `databricks bundle deploy -t dev`

### Manual deploy (local)

```bash
databricks bundle validate
databricks bundle deploy -t dev
```

Requires `DATABRICKS_HOST` and `DATABRICKS_TOKEN` in your local `~/.databrickscfg`.

## Secret Storage Model

| Secret | Store | Writer | Reader |
|---|---|---|---|
| `qbo-ap-client-id-{env}` | `qbo-ap-tokens` scope | bootstrap workflow | token manager |
| `qbo-ap-client-secret-{env}` | `qbo-ap-tokens` scope | bootstrap workflow | token manager |
| `qbo-ap-refresh-token-{realm_key}` | `qbo-ap-tokens` scope | bootstrap (initial) + token manager (rotation) | token manager |
| `qbo-ap-realm-id-{realm_key}` | `qbo-ap-tokens` scope | bootstrap workflow | token manager |

Where `env ∈ {dev, prod}` and `realm_key ∈ {sandbox, e001}`.

`env` is derived from `realm_key`:
- `sandbox → dev` (Intuit Dev keys pair with sandbox realm)
- `e001 → prod` (Intuit Prod keys pair with E001 realm)

## Phase Status

| Phase | Status |
|---|---|
| P0 — Prereqs (Intuit app, OAuth, class/account map, token manager) | In progress |
| P1 — Sheet 1 approval states | Not started (lives in `jgc-invoice-automation`) |
| P2 — Month-end JSON export to ADLS | Not started (lives in `jgc-invoice-automation`) |
| P3 — Databricks Silver layer | Not started (this repo or shared) |
| P4 — Post Bills to QBO | Not started (this repo) |
| P5 — Power BI dashboards | Not started |

See `../Invoice/AP_Pipeline_Task_Breakdown.html` for the full task-by-task plan.
