#!/bin/bash

# Database Backup Script for POS System
# This script runs a database and media backup and uploads them to S3.
# Designed for use with Cron.

# Load environment variables
# (Handled automatically by Django's python-dotenv in settings.py)
# source /var/www/pos/.env

echo "--- Starting Backup: $(date) ---"

# Navigate to project directory
cd /var/www/pos

# Activate virtual environment
source venv/bin/activate

# 1. Database Backup
echo "Backing up database..."
python manage.py dbbackup --noinput --compress

# 2. Media Files Backup (Optional but recommended)
echo "Backing up media files..."
python manage.py mediabackup --noinput --compress

# 3. Cleanup old backups (beyond 30 days)
echo "Cleaning up old backups..."
python manage.py dbbackup --clean --noinput

echo "--- Backup Complete: $(date) ---"
