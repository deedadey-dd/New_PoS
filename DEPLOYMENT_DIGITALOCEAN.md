# Digital Ocean Droplet Deployment Guide

This guide walks you through deploying the POS System on a Digital Ocean Droplet using Ubuntu, PostgreSQL, Gunicorn, and Nginx.

---

## Prerequisites

### 1. Create a Digital Ocean Droplet

1. Log in to [Digital Ocean](https://cloud.digitalocean.com/)
2. Click **Create** > **Droplets**
3. Choose the following settings:
   - **Region**: Choose closest to your users
   - **Image**: Ubuntu 22.04 LTS or 24.04 LTS
   - **Size**: Basic - Regular Intel/AMD
     - Minimum: 1 GB RAM / 1 vCPU ($6/month)
     - Recommended: 2 GB RAM / 1 vCPU ($12/month)
   - **Authentication**: SSH Key (recommended) or Password
   - **Hostname**: `pos-server` (or your preference)
4. Click **Create Droplet**

### 2. (Optional) Add a Domain

In Digital Ocean console:
1. Go to **Networking** > **Domains**
2. Add your domain and point it to your Droplet IP

---

## Step 1: Initial Server Setup

```bash
# SSH into your droplet
ssh root@YOUR_DROPLET_IP

# Create a non-root user (recommended)
adduser pos_admin
usermod -aG sudo pos_admin

# Enable firewall
ufw allow OpenSSH
ufw enable

# Switch to the new user
su - pos_admin
```

---

## Step 2: Install System Dependencies

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Install required packages
sudo apt install python3-pip python3-venv python3-dev git nginx postgresql postgresql-contrib libpq-dev curl -y
```

---

## Step 3: Configure PostgreSQL

```bash
# Start and enable PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create database and user
sudo -u postgres psql

# In psql prompt, run:
CREATE DATABASE pos_db;
CREATE USER pos_user WITH PASSWORD 'your_secure_password_here';
ALTER ROLE pos_user SET client_encoding TO 'utf8';
ALTER ROLE pos_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE pos_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE pos_db TO pos_user;
ALTER DATABASE pos_db OWNER TO pos_user;
\c pos_db
GRANT ALL ON SCHEMA public TO pos_user;
\q
```

> ⚠️ **Important**: Replace `your_secure_password_here` with a strong, unique password.

---

## Step 4: Clone & Setup the Project

```bash
# Create application directory
sudo mkdir -p /var/www/pos
sudo chown -R $USER:$USER /var/www/pos
cd /var/www/pos

# Clone your repository
git clone https://github.com/YOUR_USERNAME/New_PoS.git .

# OR upload via SCP from your local machine:
# scp -r d:\PROJECTS\New_PoS\* pos_admin@YOUR_DROPLET_IP:/var/www/pos/

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

---

## Step 5: Create Production Environment File

```bash
nano /var/www/pos/.env
```

Add the following configuration:

```env
# Django Settings
SECRET_KEY=your-super-secret-key-here
DEBUG=False
ALLOWED_HOSTS=YOUR_DROPLET_IP,your-domain.com,www.your-domain.com

# Database Configuration
DATABASE_URL=postgres://pos_user:your_secure_password_here@localhost:5432/pos_db

# Security (for production)
CSRF_TRUSTED_ORIGINS=https://your-domain.com,https://www.your-domain.com
```

**Generate a secure SECRET_KEY:**

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## Step 6: Initialize Django Application

```bash
cd /var/www/pos
source venv/bin/activate

# Collect static files
python manage.py collectstatic --noinput

# Run database migrations
python manage.py migrate

# Create superuser for admin access
python manage.py createsuperuser
```

---

## Step 7: Configure Gunicorn Service

Create a systemd service file:

```bash
sudo nano /etc/systemd/system/pos.service
```

Add the following content:

```ini
[Unit]
Description=POS System Gunicorn Daemon
Requires=pos.socket
After=network.target

[Service]
User=pos_admin
Group=www-data
WorkingDirectory=/var/www/pos
EnvironmentFile=/var/www/pos/.env
ExecStart=/var/www/pos/venv/bin/gunicorn \
          --access-logfile - \
          --error-logfile /var/log/gunicorn/error.log \
          --workers 3 \
          --bind unix:/run/pos.sock \
          pos_system.wsgi:application

[Install]
WantedBy=multi-user.target
```

Create the Gunicorn socket file:

```bash
sudo nano /etc/systemd/system/pos.socket
```

Add:

```ini
[Unit]
Description=POS System Gunicorn Socket

[Socket]
ListenStream=/run/pos.sock

[Install]
WantedBy=sockets.target
```

Create log directory and start the services:

```bash
# Create log directory
sudo mkdir -p /var/log/gunicorn
sudo chown pos_admin:www-data /var/log/gunicorn

# Reload systemd, enable and start services
sudo systemctl daemon-reload
sudo systemctl enable pos.socket pos.service
sudo systemctl start pos.socket pos.service

# Verify the service is running
sudo systemctl status pos.service
```

---

## Step 8: Configure Nginx

Create the Nginx site configuration:

```bash
sudo nano /etc/nginx/sites-available/pos
```

Add:

```nginx
server {
    listen 80;
    server_name YOUR_DROPLET_IP your-domain.com www.your-domain.com;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Max upload size (adjust if needed)
    client_max_body_size 10M;

    # Favicon
    location = /favicon.ico {
        access_log off;
        log_not_found off;
    }

    # Static files
    location /static/ {
        alias /var/www/pos/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Media files
    location /media/ {
        alias /var/www/pos/media/;
        expires 7d;
    }

    # Proxy to Gunicorn
    location / {
        include proxy_params;
        proxy_pass http://unix:/run/pos.sock;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

Enable the site and test the configuration:

```bash
# Enable the site
sudo ln -s /etc/nginx/sites-available/pos /etc/nginx/sites-enabled/

# Remove default site (optional)
sudo rm /etc/nginx/sites-enabled/default

# Test nginx configuration
sudo nginx -t

# Restart nginx
sudo systemctl restart nginx
```

---

## Step 9: Configure Firewall

```bash
# Allow HTTP and HTTPS traffic
sudo ufw allow 'Nginx Full'

# Verify firewall status
sudo ufw status
```

---

## Step 10: Setup SSL with Let's Encrypt (Recommended)

```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx -y

# Obtain SSL certificate (replace with your domain)
sudo certbot --nginx -d your-domain.com -d www.your-domain.com

# Verify auto-renewal is set up
sudo systemctl status certbot.timer
```

---

## Step 11: (Optional) Configure Digital Ocean Firewall

For added security, configure at the cloud level:

1. Go to **Networking** > **Firewalls** in Digital Ocean console
2. Create a new firewall with these rules:

**Inbound Rules:**
| Type  | Protocol | Port Range | Sources    |
|-------|----------|------------|------------|
| SSH   | TCP      | 22         | Your IP only |
| HTTP  | TCP      | 80         | All IPv4/IPv6 |
| HTTPS | TCP      | 443        | All IPv4/IPv6 |

3. Apply the firewall to your Droplet

---

## Useful Commands

### Service Management

```bash
# Restart application after code changes
sudo systemctl restart pos

# View application logs
sudo journalctl -u pos -f

# View Gunicorn error logs
tail -f /var/log/gunicorn/error.log

# View Nginx access/error logs
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### Deployment Updates

```bash
cd /var/www/pos
source venv/bin/activate

# Pull latest code
git pull origin main

# Install any new dependencies
pip install -r requirements.txt

# Run migrations
python manage.py migrate

# Collect static files
python manage.py collectstatic --noinput

# Restart the application
sudo systemctl restart pos

# (Optional) Restart nginx if config changed
sudo systemctl restart nginx
```

### Database Backup

```bash
# Create a backup
sudo -u postgres pg_dump pos_db > /var/backups/pos_db_$(date +%Y%m%d_%H%M%S).sql

# Restore from backup
sudo -u postgres psql pos_db < /var/backups/pos_db_backup.sql
```

---

## Monitoring with Digital Ocean (Optional)

1. **Enable Droplet Metrics**: Already enabled by default
2. **Set Up Alerts**:
   - Go to **Monitoring** in Digital Ocean console
   - Create alerts for CPU, Memory, and Disk usage
3. **Install Monitoring Agent** (for enhanced metrics):
   ```bash
   curl -sSL https://repos.insights.digitalocean.com/install.sh | sudo bash
   ```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| **502 Bad Gateway** | Check if Gunicorn is running: `sudo systemctl status pos` |
| **Static files not loading** | Run `python manage.py collectstatic` and verify Nginx paths |
| **Database connection error** | Verify DATABASE_URL in `.env` and check PostgreSQL: `sudo systemctl status postgresql` |
| **Permission denied on socket** | Ensure user/group in `pos.service` matches your setup |
| **SSL certificate issues** | Run `sudo certbot renew --dry-run` to test renewal |
| **500 Internal Server Error** | Check logs: `sudo journalctl -u pos -n 100` |

### Quick Diagnostics

```bash
# Check if services are running
sudo systemctl status pos nginx postgresql

# Test Gunicorn socket
curl --unix-socket /run/pos.sock http://localhost/

# Check ports in use
sudo ss -tulpn | grep -E '80|443'

# Check disk space
df -h

# Check memory usage
free -h
```

---

## Performance Optimization Tips

1. **Adjust Gunicorn workers**: Set workers to `(2 x CPU cores) + 1`
2. **Enable gzip compression** in Nginx:
   ```nginx
   gzip on;
   gzip_types text/plain text/css application/json application/javascript text/xml application/xml;
   ```
3. **Configure PostgreSQL**: Tune `postgresql.conf` for your RAM size
4. **Use CDN**: Consider DigitalOcean Spaces + CDN for static/media files

---

## Quick Reference

| Component | Config Location | Logs |
|-----------|-----------------|------|
| Gunicorn Service | `/etc/systemd/system/pos.service` | `journalctl -u pos` |
| Nginx | `/etc/nginx/sites-available/pos` | `/var/log/nginx/` |
| PostgreSQL | `/etc/postgresql/*/main/` | `/var/log/postgresql/` |
| Application | `/var/www/pos/` | `/var/log/gunicorn/` |
| SSL Certs | `/etc/letsencrypt/` | `certbot certificates` |
