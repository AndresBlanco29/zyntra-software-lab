# QuickBooks alignment sync (every 6 hours)

This project keeps Tortilla and QuickBooks aligned with a scheduled job and manual buttons in **QuickBooks Center → Keep both systems aligned**.

## What runs automatically

Every **6 hours** at **6:00 AM, 12:00 PM, 6:00 PM, and 12:00 AM** in **US Eastern** (`America/New_York`):

### Import from QuickBooks → Tortilla
- New or updated **customers**
- New or updated **catalog items** (products)
- **Payment status** refresh for invoices already linked to QuickBooks (paid / due / overdue)

### Export from Tortilla → QuickBooks
- **New customers only** (no `quickbooks_id` yet)
- **New products only** (presentations without `quickbooks_id`)

### Never automatic
- **Invoices** and adjustment notes stay **manual** to avoid incomplete accounting exports.

## Management command

```bash
python manage.py run_scheduled_quickbooks_sync
```

Options:
- `--force` — run immediately, ignoring schedule window and last-slot tracking
- `--now=2026-07-03T06:00:00` — test a specific US Eastern datetime

## Railway cron

Run the checker **once per hour**. The command itself decides whether the current US Eastern hour is `0`, `6`, `12`, or `18`.

Example Railway cron service (same pattern as `run_scheduled_backups`):

```bash
python manage.py run_scheduled_quickbooks_sync
```

Schedule expression (UTC on Railway): `0 * * * *` (top of every hour).

The app stores the last completed slot in `QuickBooksConnection.sync_state.alignment_automation.last_slot` so the same hour is not processed twice.

## Manual sync

In **QuickBooks Center**:
- **Run pull sync now** — incremental alignment (uses saved cursors)
- **Run full resync** — ignores cursors and refreshes all linked invoice statuses

Both buttons use the same import/export rules as the scheduled job.

## Sync history

Open **QuickBooks → Sync history** in the sidebar to review each run:
- start time and scheduled slot
- customers/catalog imported
- invoice payment statuses updated
- new customers/products exported
- failures and duration

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `QUICKBOOKS_ALIGNMENT_TIMEZONE` | `America/New_York` | Schedule timezone |
| `QUICKBOOKS_CATALOG_SYNC_SKIP_IMAGES` | `true` | Faster catalog import during sync |
