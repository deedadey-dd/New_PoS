# Backblaze B2 Backup Guide

This guide covers how to manage automated backups for your POS system using Backblaze B2.

## Automated Backups

We use `django-dbbackup` to send database and media dumps to **Backblaze B2 Object Storage**.

### 1. Requirements
Ensure these are in your `requirements.txt` and installed:
- `django-dbbackup`
- `django-storages`
- `boto3`

### 2. Environment Variables
Add these to your `/var/www/pos/.env` file:

```env
# Backup Configuration (Backblaze B2 Object Storage)
BACKUP_S3_ACCESS_KEY=your_b2_application_key_id
BACKUP_S3_SECRET_KEY=your_b2_application_key
BACKUP_S3_BUCKET_NAME=your_b2_bucket_name
BACKUP_S3_ENDPOINT_URL=https://s3.us-west-004.backblazeb2.com  # Replace with your actual B2 S3 endpoint
BACKUP_S3_REGION=us-west-004  # Replace with your actual B2 region
```

### 3. Automated Cron Job (Midnight)

To automate the backup, add a entry to your server's crontab:

1. Open crontab:
   ```bash
   crontab -e
   ```

2. Add this line at the bottom (adjust paths if different):
   ```bash
   0 0 * * * /bin/bash /var/www/pos/scripts/backup.sh >> /var/www/pos/logs/backup.log 2>&1
   ```

This will run the backup every night at **00:00 (Midnight)**.

---

## Manual Backup Commands

If you ever need to run a backup manually:

```bash
# Activate your environment
cd /var/www/pos
source venv/bin/activate

# Backup Database
python manage.py dbbackup

# Backup Media
python manage.py mediabackup

# Restore Database (WARNING: This overwrites current data!)
python manage.py dbrestore
```

## Deletion Policy (Retention)

The current configuration keeps the last **30 days** of backups locally tracked. We recommend setting a **Lifecycle Rule** in your Backblaze B2 Bucket settings to automatically delete backups older than 30 days for additional safety and to minimize storage costs.
