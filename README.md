# ERPNext Banking

Custom Frappe app for ERPNext that pulls bank transactions into `Bank Transaction` and
automatically reconciles them against `Sales Invoice`, `Payment Request` and `Purchase Invoice`
via the `variable_symbol` (VS) custom field.

First provider: **Fio Banka** (Czech Republic). Architecture is provider-pluggable — adding a
second bank is drop-in (see `providers/` directory).

**Versions:** Frappe 16.20+, ERPNext 16.21+. The JMHZ module needs Frappe 16.60+, ERPNext 16.60+
and **HRMS 16.60+**.

> **Requires HRMS.** Since the JMHZ module was added, `required_apps = ["erpnext", "hrms"]`: the
> app can no longer be installed on a site without Frappe HR (its doctypes link to Salary Slip and
> Additional Salary).

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

## Card payments & rules (Bank Matching Rule)

Card charges have **no counterparty account** — the bank puts the card number in
`reference_number` and the merchant string in the description (`Nákup: ANTHROPIC* CLAUDE SUB,
…, částka  90.00 EUR`). The supplier/VS rules can't match those, so a **Bank Matching Rule**
doctype drives them. During outgoing reconciliation, for a BT with no supplier, the first
enabled rule (lowest `priority`) whose `pattern` is found (case-insensitively, or as a regex
when prefixed `re:`) in the description decides the action:

- **`merchant_supplier`** — find the single open Purchase Invoice of the rule's supplier whose
  `bill_date` is within the card window (BT date −35 … +7 days) and whose amount matches, then
  create a submitted Payment Entry linked to the BT. Matching order per candidate:
  1. original currency **CZK** → exact CZK amount (±1 Kč);
  2. foreign currency → compare the parsed `částka` against the PI custom fields
     `original_amount` / `original_currency` (populated by the Paperless import for EUR/USD
     invoices);
  3. fallback → **relative FX tolerance** (default 4 %) of BT withdrawal vs PI outstanding,
     because the bank converts at its card rate while the invoice is booked at the ČNB rate
     (e.g. 2241.84 vs 2177.55 = 2.95 % → match). Ambiguity (0 or >1 candidates) is left for
     manual reconciliation.

  When the bank amount differs from the invoice, the FX difference is written off on the
  Payment Entry via a `deductions` row: **563 - Kurzové ztráty** (bank charged more) or
  **663 - Kurzové zisky** (bank charged less), so the entry balances.
- **`auto_je`** — book a no-document withdrawal (salary, bank fee) straight to an account via a
  submitted **Journal Entry** (`Bank Entry`: debit the rule's `je_account` + cost center, credit
  the bank contra account **221**), then link it to the BT. Gated by **Auto Journal Entries** in
  Fio Settings (default on). Salary rules match the counterparty account the mapper writes into
  the description (`Protiúčet: 2371741018/3030` → 521); fees match `Poplatek` → 568.
- **`ignore`** — never auto-process transactions matching this pattern.

Defaults (FX tolerance, loss/gain accounts, JE contra account, cost center) live in the
**Card Payments & Rules** section of Fio Settings. A `migrate` seeds the default rules
(FACEBK, ANTHROPIC, GOOGLE, HETZNER, salary/fee auto_je rules) — but only when the referenced
Supplier / Account already exists, so review **Bank Matching Rule** list after install and add
any that were skipped.

## Incoming fallback matching (wrong/missing VS)

Payers sometimes send the wrong variable symbol (typo'd, copy-pasted from a different invoice,
or omitted entirely), so the exact-VS match (`reference_number == variable_symbol`) finds
nothing even though the payment is legitimate. When that happens, `_reconcile_incoming` tries
two fallback layers before giving up:

1. **Customer via counter-account.** Same idea as the outgoing supplier lookup: the BT
   description carries `Protiúčet: <acc>/<bank>` (written by the Fio mapper). If a `Bank
   Account` record with that account number + bank code (or, failing that, matching
   `bank_account_no`) has `party_type = Customer`, we know *who* paid. If exactly one of that
   Customer's open Sales Invoices matches the deposit amount (±1 Kč), it's reconciled. Zero or
   several matching invoices → left `Unreconciled` (a wrong amount from a known customer is a
   real discrepancy, not something to paper over with a wider search).
2. **Company-wide amount + window.** Only when no Customer could be identified from the
   counter-account (or the description carries no `Protiúčet:` token at all — e.g. some other
   bank/provider format): every open Sales Invoice across the company with `outstanding_amount`
   matching the deposit (±1 Kč) and `posting_date` within **BT date −35 … +7 days**. Exactly one
   candidate → match; otherwise left `Unreconciled`.

Both layers are gated by the same `Auto-Reconcile Incoming` flag as the exact-VS match — there's
no separate on/off switch. Exact-VS matching itself is unchanged: when the VS does resolve to a
Sales Invoice or Payment Request, these fallbacks never run.

## JMHZ module (salary slips from monthly employer reports)

A separate Frappe module **JMHZ** in this app imports the Czech monthly employer report
(*jednotné měsíční hlášení zaměstnavatele*, XML schema 1.4 and 1.5) into HRMS Salary Slips. HRMS
computes nothing: every amount comes from the report and the slip only adds it up to the same
numbers. The banking part does not depend on it.

**Setup.** Nothing has to be created by hand. The patch `jmhz_initial_setup` and every `migrate`
(`after_migrate`) run an idempotent setup that:

- creates the JMHZ salary components and enforces their flags (`depends_on_payment_days = 0`,
  `is_tax_applicable = 0`, no formula);
- creates and submits the salary structure `Import z JMHZ` with all components at amount 0;
- sets Payroll Settings: `disable_rounded_total = 1` and the built-in salary slip e-mail **off**
  (it is switched off again on every migrate);
- creates `JMHZ Settings` (company from Global Defaults);
- assigns a technical holiday list without holidays to the company, but only if the company has
  none (HRMS needs one to create any slip).

All JMHZ doctypes (`JMHZ Import`, `JMHZ Person`, `JMHZ Settings`) are for **HR Manager** only.
System Manager gets no access.

**Monthly flow** (workspace **JMHZ**, HR Manager):

1. **Nahrát JMHZ XML** on the JMHZ Import list. The file is parsed in memory and never stored as
   a File; only its hash, name and the parsed numbers are kept.
2. **Preview:** one row per person. Pick the Employee for unpaired rows, or skip a row with a
   reason. The pairing is remembered in `JMHZ Person`.
3. **Vytvořit pásky:** Additional Salaries and draft Salary Slips, in one transaction. Every slip
   is compared with the report line by line; any difference rolls everything back.
4. Optional: **Nahrát PDF pásky** and **Zkontrolovat PDF pásky** (see *Printed slips* below). Every
   printed slip has to be reviewed by a person before the next step.
5. **Odeslat pásky:** submits the slips. Employees then see them in the desk and in `/hrms`.

**Informative values.** Besides the slip amounts the parser reads values that only describe the
slip: the monthly tax credits, the average hourly earnings, the kind of employment, the split of
bonuses and overtime hours, the employer contributions and the exempt income. They are stored on
the import row, never change an amount and never block a person; a value that cannot be read is
left out with a warning (`P14`). Only amounts are read: who the children or the spouse are is
never read and never stored. Rows imported by an older version do not have these values.

**Corrections.** A corrective report (`O`) replaces the slips of the persons it contains, the
others stay unchanged. A regular report from a different file for the same period replaces the
earlier import only after HR confirms it. A cancelling report (`S`) cancels all JMHZ slips of the
period with **Provést storno**. **Zrušit import** cancels the slips and Additional Salaries of one
import; the same file can then be imported again.

**Printed slips (optional).** In preview or after creating the slips, **Nahrát PDF pásky** takes
the summary report of printed payroll slips from the payroll system. Each slip is cut out and
matched to a row by its amounts (the name confirms and breaks ties). A report of another month is
refused.

The whole slip is read, not only its totals: worked time, leave, wage components, tax base and
credits, employer payments, other payments, deductions, the way of payment. Reading is positional
(`pdf_slips.ZONES` and `SPECS`), so it has to be measured again when the layout of the report
changes. Twelve control fields are required; any other value that cannot be read is listed as
unread and is never replaced by zero.

Three checks, any failure blocks **Odeslat pásky** until HR confirms it:

- the 12 control fields against the JMHZ report (a manual deduction entered later is checked again);
- tax credits, average earnings, kind of employment, exempt income and employer insurance against
  the informative values of the report, where both sides have them;
- the internal sums of the slip: leave balance, rounded tax base, tax, advance after credits and
  the gross wage as the sum of its components.

**Review.** Reading a printed slip is fragile, and values that exist only on the printed slip
(leave, wage rate) have nothing to be checked against. So every row with a printed slip has to be
looked at: **Zkontrolovat PDF pásky** shows the cut next to the values read from it, with unread
values highlighted. HR can correct any value outside the 12 control fields (an empty value means
"nothing is printed there") and then confirms the row; a mismatch has to be accepted in the same
step. A correction or a different report cancels the confirmation, the same report uploaded again
keeps it. **Odeslat pásky** is refused while a row with a printed slip is not confirmed. A row
without a printed slip blocks nothing.

**Full slip.** Everything the slip shows beyond its amounts is stored on the Salary Slip in
`jmhz_slip_data` (JSON, see `slip_data.py`): the printed slip block by block and the informative
values of the report. It is written when the slip is created and rewritten on the draft whenever
the printed slip, a correction or the review changes; a submitted slip is never changed. A print
format should read only this field and the `jmhz_*` fields, never the import, which employees
cannot read. The totals in it come from the JMHZ report even when HR accepted a mismatch. Slips
created by an older version have no such data and print as before; to add it, cancel the import
and import the month again with its report.

The report and the cuts are private Files attached to the JMHZ Import, so only HR Manager can open
them, and they stay there after the slips are submitted. They must never be sent to employees: the
report holds the slips of everybody, and cropping a cut hides the rest of its page but does not
remove it from the file. A new report replaces the previous one. **Smazat PDF** removes the report
and the cuts; cancelling the import removes them too.

**E-mail.** The built-in HRMS e-mail on submit stays off. `JMHZ Settings.email_mode` decides who
gets the slip after **Odeslat pásky**: `Vypnuto` (default, nobody), `Jen testovací zaměstnanci`
or `Všem` (everyone with a preferred e-mail). The same mode applies to the button on the slip and
to the HRMS list action "Email Salary Slips". Test the e-mail on a test employee with a synthetic
report before switching to `Všem`.

**Guards** (can be relaxed in JMHZ Settings where noted):

- a JMHZ slip is submitted and cancelled only from its import, never directly or in bulk from the
  Salary Slip list;
- a manually created Salary Slip cannot be submitted and a Payroll Entry cannot be saved, unless
  JMHZ Settings allow it;
- users without HR Manager / HR User see only submitted slips;
- deleting a JMHZ document leaves no IK MPSV and no amounts in Deleted Document.

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
- For incoming with a wrong/missing VS: see "Incoming fallback matching" above — check whether
  the description has a `Protiúčet:` token resolving to a Customer, and whether more than one
  open Sales Invoice matches the amount (ambiguity → left Unreconciled on purpose).

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
