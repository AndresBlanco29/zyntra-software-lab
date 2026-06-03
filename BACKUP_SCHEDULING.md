# Database Backup Scheduling

This project already includes the management command below:

```powershell
venv\Scripts\python.exe manage.py backup_database --label=daily --keep=14
```

Use it to create a compressed `.json.gz` backup under `media/backups/database/`.

For full recovery including uploaded PDFs and images, use the full system backup command:

```powershell
venv\Scripts\python.exe manage.py backup_system --label=daily --keep=14
```

For production automation driven by the saved preference in Database Backups, use:

```powershell
venv\Scripts\python.exe manage.py run_scheduled_backups --keep=14
```

This command reads the saved cadence (`daily`, `weekly`, or `monthly`) and:
- creates a full system backup only when the selected cadence is due
- skips duplicate runs on the same day
- records the last successful automated run in `QuickBooksConnection.sync_state`

## Recommended schedules

Daily backup:

```powershell
python manage.py backup_database --label=daily --keep=14
```

Weekly backup:

```powershell
python manage.py backup_database --label=weekly --keep=8
```

Daily full backup with media:

```powershell
python manage.py backup_system --label=daily --keep=14
```

Weekly full backup with media:

```powershell
python manage.py backup_system --label=weekly --keep=8
```

Meaning:
- `--label=daily` or `--label=weekly` adds that label to the file name.
- `--keep=14` keeps only the newest 14 backups of that schedule run.
- `--keep=8` keeps only the newest 8 backups of that schedule run.

Production note:
- Full backups now collect files referenced by Django file fields, even when production media is backed by Cloudinary instead of the local container filesystem.
- When `USE_CLOUDINARY_MEDIA=True`, backup files are stored through Cloudinary raw storage so `.json.gz` and `.tar.gz` files are handled as backup artifacts instead of image uploads.

## Railway

Railway normally runs scheduled jobs as separate commands.

Suggested daily job:

```bash
python manage.py run_scheduled_backups --keep=14
```

Suggested weekly job:

```bash
No separate weekly job is required if you use `run_scheduled_backups` once per day.
```

Suggested cadence:
- Run the scheduler once per day, for example at 2:00 AM.
- The command itself decides whether today should create a daily, weekly, or monthly backup.

Suggested Railway setup:
1. Open your Railway project.
2. Go to the service that runs this Django app.
3. Create a scheduled job.
4. Use the `run_scheduled_backups` command above.
5. Make sure the job uses the same project variables as the web service.

Important note for Railway:
- If your app uses ephemeral disk on the running container, local backup files may not be durable long term.
- In that case, keep using the in-app download and also consider sending backups to external storage later.

## Windows Task Scheduler

Use the project folder as the start directory:

```text
C:\Users\HOME\OneDrive\Desktop\PROYECTO TORTILLA RAILWAY\Proyecto App Tortilla
```

Program/script:

```text
C:\Users\HOME\OneDrive\Desktop\PROYECTO TORTILLA RAILWAY\Proyecto App Tortilla\venv\Scripts\python.exe
```

Arguments for daily backup:

```text
manage.py run_scheduled_backups --keep=14
```

Steps:
1. Open Task Scheduler.
2. Create Task.
3. In General, choose a name like `LTG Daily Database Backup`.
4. In Triggers, add a Daily trigger at 2:00 AM.
5. In Actions, choose Start a program.
6. Put the Python executable path in Program/script.
7. Put the command arguments shown above in Add arguments.
8. Put the project path in Start in.
9. Save the task.
10. No second weekly task is required when using `run_scheduled_backups`.

## Manual verification

Run this manually when needed:

```powershell
venv\Scripts\python.exe manage.py backup_system --label=manual --keep=20
```

Then verify:
- A new file appears in `media/backups/database/`
- The file name ends with `.json.gz`
- The file is listed in the QuickBooks backup panel
- The file can be downloaded again from the UI

## Restore from backup

The project also includes a restore command.

Restore into an already migrated but empty database:

```powershell
venv\Scripts\python.exe manage.py restore_database --file=ltg-system-backup-daily-20260520-020000.tar.gz --confirm=RESTORE
```

Restore after wiping existing records first:

```powershell
venv\Scripts\python.exe manage.py restore_database --file=ltg-system-backup-daily-20260520-020000.tar.gz --flush --confirm=RESTORE
```

Important:
- Run `manage.py migrate` before restoring into a fresh database.
- `--flush` deletes current records before loading the backup.
- The command requires `--confirm=RESTORE` on purpose because it is destructive.
- Restoring from `.tar.gz` brings back both database records and uploaded files from `media/`.
- Restoring from `.json.gz` brings back only database records.
