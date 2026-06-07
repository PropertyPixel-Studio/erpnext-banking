# ERPNext Banking

Custom Frappe app for ERPNext that pulls bank transactions into `Bank Transaction` and
automatically reconciles them against `Sales Invoice`, `Payment Request` and `Purchase Invoice`
via the `variable_symbol` (VS) custom field.

First provider: **Fio Banka** (Czech Republic). Architecture is provider-pluggable — adding a
second bank is drop-in (see `providers/` directory).

**Versions:** Frappe 16.20+, ERPNext 16.21+.

## Install

Standard Frappe bench flow. Add this repo to your bench's `apps` and install on the site.

```bash
# In your bench directory (typically /home/frappe/frappe-bench inside the container)
cd /home/frappe/frappe-bench

# 1) BACKUP FIRST — install runs migrate which is a schema change
bench --site erp.propix.cz backup --with-files

# 2) Install
bench get-app erpnext_banking https://github.com/PropertyPixel-Studio/erpnext-banking --branch main
bench --site erp.propix.cz install-app erpnext_banking
bench --site erp.propix.cz migrate

# 3) Restart workers so the scheduler picks up the new cron jobs
bench restart
```

For the **Dokploy-managed** install (this is the normal path): the app gets baked into the
ERPNext image via `apps.json`. Add this entry to `erpnext-jira-image/apps.json`:

```json
{ "url": "https://github.com/PropertyPixel-Studio/erpnext-banking", "branch": "main" }
```

Push, let GitHub Actions rebuild, then `Redeploy` in Dokploy. Volumes stay intact. After the
container restarts, run the `bench backup`, `install-app`, `migrate`, `restart` sequence above
from the Dokploy console of the backend container.

## Configure

In the ERPNext desk, search for **Fio Settings** (Single doctype):

| Field | Value |
|---|---|
| Enabled | tick on once everything below is filled |
| API Token | **dedicated read-only Fio token** (see security note below); pasted into the password field — it's stored encrypted with the site's encryption key |
| Company | the company that owns the bank account |
| Bank Account | the ERPNext Bank Account record for your Fio account (must exist; must be linked to a GL account) |
| Lookback Days | default `7` — window for the daily safety-net fetch |
| Reconcile Window Days | default `90` — sliding window for the re-match cron |
| Auto-Reconcile Incoming | leave **off** for the first run; turn on after verifying ingest |
| Auto-Reconcile Outgoing | leave **off** for the first run; turn on after incoming is stable |
| Notify on Error | tick on |
| Notify Email | who gets the error mail |

Then:

1. Click **Dry-run** — fetches Fio data, parses it, but writes nothing. Inspect the resulting `Bank Sync Log`.
2. Click **Sync Now** — first real ingest. Confirm `Bank Transaction` records appear.
3. Click **Sync Now** again — should report `created=0, skipped_duplicates>0` (dedup works).
4. When confident, turn on **Auto-Reconcile Incoming** and click **Run Reconciliation**.
5. After watching incoming for a few days, turn on **Auto-Reconcile Outgoing**.

The three nightly cron jobs (sync at 02:00, lookback at 03:00, re-match at 03:30) start as soon
as `Enabled` is on.

## Security

- The Fio token is a **read-only** token (Fio supports issuing tokens limited to transaction
  export — no payment rights). **Don't reuse a token that has payment scope.**
- The token is stored encrypted in the Fio Settings password field, using the site's
  `encryption_key` from `site_config.json`. The standard ERPNext backup (`bench backup --with-files`)
  includes `site_config_backup.json`, so the token can be restored from backup.
- Mirror the token in VaultWarden as the master copy. Don't commit it to git.
- Logs mask the token automatically — it appears as `***` in `Bank Sync Log.endpoint`,
  `error_log`, and `frappe.log_error` entries.
- **Dedicated token per app.** Fio's incremental pointer (`/last/`) is shared per token across
  all clients of that token. Sharing a token with anything else causes pointer collisions and
  silently lost transactions. Get a separate token for this app.

## Troubleshoot

### 1. "Invalid API token" (Fio returns HTTP 500)

Fio uses HTTP 500 (not 401/403) for invalid tokens. Symptoms:
- `Bank Sync Log.status = Error`, `error_log` contains `FioAuthError`.
- Notify email arrives (rate-limited to 1× / 24 h per error class).

The app **does not** auto-disable on this error — every night's cron will retry. Fix by:
1. Verify the token in Fio internet banking (Settings → API).
2. If it was rotated, paste the new token into Fio Settings → API Token.
3. Save. Next cron run (or click Sync Now) should recover.

### 2. "Rate limited" (HTTP 409)

Fio allows 1 request per 30 s per token. The app guards against this with a 35-second server-side
throttle on manual triggers. If you still see 409:
- You're sharing the token with another client. **Get a dedicated token.**
- Two scheduler jobs ran too close together. Default cron times (02:00 / 03:00 / 03:30) have 30+ min
  gaps, but if you customized them, check.

### 3. "Bank Account is required when Fio is enabled"

The `Bank Account` field on Fio Settings is mandatory once `Enabled` is on. You need to create the
ERPNext Bank Account record first:
- ERPNext desk → search "Bank Account" → New
- Fill: Account Name, Bank (link), Company, Account (the GL account this maps to — typically a
  "Bank" type account).
- Save, then point Fio Settings at it.

### 4. Transactions not appearing despite Fio reporting them

Two layers can hide this:
1. **Dedup**: Bank Sync Log shows `skipped_duplicates > 0`. Check whether the BT already exists
   (search by `transaction_id` in `Bank Transaction` list).
2. **Fio pointer drift**: `/last/` moves Fio's server-side pointer past records you might want.
   The 03:00 lookback (`/periods/`) for the past 7 days normally catches this. If you need
   manual recovery, a System Manager can call the recovery endpoint:
   ```
   bench --site erp.propix.cz execute erpnext_banking.api.fio_reset_pointer --kwargs "{'transaction_id': '12345'}"
   ```
   (Sets Fio's pointer back to a specific ID. Use with care — no UI button on purpose.)

### 5. Auto-reconcile didn't match a payment that should match

Check, in order:
- Is `auto_reconcile_incoming` / `auto_reconcile_outgoing` actually on in Fio Settings?
- Does the BT have a `reference_number` populated? (For Fio that's `column5` = VS.)
- Does the matching Sales Invoice / Purchase Invoice have `variable_symbol` set, with the
  same value? (The custom server scripts `Automatic-variable-symbol` /
  `Automatic-variable-for-payment-request` populate this — verify they're enabled.)
- Does `outstanding_amount > 0` on the invoice?
- Is the amount within ±1 Kč of `outstanding_amount`?
- For outgoing only: do you require 2+ matching signals? VS alone won't match outgoing without
  either a supplier identification or a single matching open PI.

Anything that doesn't auto-match stays `Unreconciled` and is visible in the Bank Reconciliation
Tool for manual resolution. **Nothing is ever lost.**

## Adding a second bank

The architecture is intentionally provider-pluggable. To add (for example) ČSOB:

1. `providers/csob/` with `__init__.py` (a `CsobProvider` class), `client.py`, `mapper.py`.
   Inherit from `providers.base.BankProvider`, implement four methods: `is_enabled`,
   `fetch_new`, `fetch_period`, `to_bank_transaction`.
2. New Single doctype `CSOB Settings` under `erpnext_banking/erpnext_banking/doctype/csob_settings/`
   with whatever auth fields ČSOB needs.
3. Register the class in `providers/__init__.py`:
   ```python
   from .csob import CsobProvider
   ALL_PROVIDERS = [FioProvider, CsobProvider]
   ```

`sync.py`, `reconcile.py`, `hooks.py`, `Bank Sync Log`, the workspace, and the cron schedule all
stay untouched. The Fio side is unaffected.

## License

MIT (see `license.txt`).
