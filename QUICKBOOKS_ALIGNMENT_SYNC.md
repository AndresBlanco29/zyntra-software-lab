# QuickBooks alignment sync (every 6 hours)

This project keeps Tortilla aligned with QuickBooks using a scheduled background job and manual buttons in **QuickBooks Center → Keep both systems aligned**.

## What runs automatically

Every **6 hours** at **6:00 AM, 12:00 PM, 6:00 PM, and 12:00 AM** in **US Eastern** (`America/New_York`):

### Import from QuickBooks → Tortilla
- New or updated **customers**
- New or updated **catalog items** (products)
- **Inventory quantities** from QuickBooks (`QtyOnHand`) overwrite local stock for linked inventory items
- **Payment status** refresh for invoices already linked to QuickBooks (paid / due / overdue)

### Never automatic
- **Export** to QuickBooks (customers, products, invoices, notes) — use **Send local records to QuickBooks** manually
- **Invoices** and adjustment notes stay **manual**

## Why sync history may be empty

The web app alone does **not** run the scheduler. You need a **second Railway service** (worker) or a **cron job** that calls the management command.

## Railway setup (required for automatic runs)

### Option A — worker service (recommended)

1. In Railway, add a **new service** from the same GitHub repo.
2. Set the start command to:

```bash
python manage.py run_production_scheduler_daemon
```

3. Use the **same environment variables** as the web service (database, QuickBooks keys, etc.).
4. Do **not** expose a public port on this service.

The daemon wakes every hour and runs the alignment check. The check itself only imports when the US Eastern hour is `0`, `6`, `12`, or `18`.

### Option B — Railway cron (hourly)

```bash
python manage.py run_production_scheduler_daemon --once
```

Schedule: `0 * * * *` (top of every hour, UTC).

## Management commands

```bash
python manage.py run_scheduled_quickbooks_sync
```

Options:
- `--force` — run immediately, ignoring schedule window and last-slot tracking
- `--now=2026-07-03T06:00:00` — test a specific US Eastern datetime

## Manual sync

In **QuickBooks Center**:
- **Run pull sync now** — incremental import (uses saved cursors)
- **Run full resync** — ignores cursors and refreshes all linked invoice statuses

Both buttons import only. They do **not** export to QuickBooks.

## Sync history

Open **QuickBooks → Sync history** in the sidebar to review each run:
- start time and scheduled slot
- customers/catalog imported
- invoice payment statuses updated
- export column shows **Manual only** for import-only runs

The app stores the last completed slot in `QuickBooksConnection.sync_state.alignment_automation.last_slot` so the same hour is not processed twice.
