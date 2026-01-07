# Oracle Cloud Free Tier Deployment Guide

## Prerequisites Installed ✅
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv git nginx postgresql postgresql-contrib -y
```

---

## Step 1: Configure PostgreSQL

```bash
# Start PostgreSQL
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create database and user
sudo -u postgres psql

# In psql prompt:
CREATE DATABASE pos_db;
CREATE USER pos_user WITH PASSWORD 'your_secure_password_here';
ALTER ROLE pos_user SET client_encoding TO 'utf8';
ALTER ROLE pos_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE pos_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE pos_db TO pos_user;
\q
```

---

## Step 2: Clone & Setup Project

```bash
# Create app directory
sudo mkdir -p /var/www/pos
sudo chown $USER:$USER /var/www/pos
cd /var/www/pos

# Clone your repository (or upload files via SCP)
git clone https://github.com/YOUR_USERNAME/New_PoS.git .
# OR upload via SCP:
# scp -r d:\PROJECTS\New_PoS\* ubuntu@YOUR_SERVER_IP:/var/www/pos/

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn psycopg2-binary
```

---

## Step 3: Create Production .env

```bash
nano /var/www/pos/.env
```

Add these contents:
```
SECRET_KEY=your-super-secret-key-generate-one-with-python
DEBUG=False
ALLOWED_HOSTS=your-server-ip,your-domain.com
DATABASE_URL=postgres://pos_user:your_secure_password_here@localhost:5432/pos_db
```

Generate a secret key:
```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## Step 4: Initialize Django

```bash
cd /var/www/pos
source venv/bin/activate

# Collect static files
python manage.py collectstatic --noinput

# Run migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser
```

---

## Step 5: Setup Gunicorn Service

```bash
sudo nano /etc/systemd/system/pos.service
```

Add:
```ini
[Unit]
Description=POS System Gunicorn Daemon
After=network.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=/var/www/pos
ExecStart=/var/www/pos/venv/bin/gunicorn --workers 3 --bind unix:/var/www/pos/pos.sock pos_system.wsgi:application

[Install]
WantedBy=multi-user.target
```

Start the service:
```bash
sudo systemctl start pos
sudo systemctl enable pos
sudo systemctl status pos
```

---

## Step 6: Configure Nginx

```bash
sudo nano /etc/nginx/sites-available/pos
```

Add:
```nginx
server {
    listen 80;
    server_name YOUR_SERVER_IP your-domain.com;

    location = /favicon.ico { access_log off; log_not_found off; }
    
    location /static/ {
        alias /var/www/pos/staticfiles/;
    }

    location /media/ {
        alias /var/www/pos/media/;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/var/www/pos/pos.sock;
    }
}
```

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/pos /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## Step 7: Configure Oracle Cloud Firewall

In Oracle Cloud Console:
1. Go to **Networking > Virtual Cloud Networks**
2. Click your VCN > **Security Lists**
3. Add **Ingress Rules**:
   - Source: `0.0.0.0/0`, Port: `80` (HTTP)
   - Source: `0.0.0.0/0`, Port: `443` (HTTPS)

On the instance:
```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

---

## Step 8: (Optional) HTTPS with Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```

---

## Useful Commands

```bash
# View logs
sudo journalctl -u pos -f

# Restart services after code changes
sudo systemctl restart pos
sudo systemctl restart nginx

# Update code from git
cd /var/www/pos
git pull
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart pos
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| 502 Bad Gateway | Check if gunicorn is running: `sudo systemctl status pos` |
| Static files not loading | Run `python manage.py collectstatic` and check nginx config |
| Database connection error | Verify DATABASE_URL in .env and PostgreSQL is running |
| Permission denied on socket | Ensure `User=ubuntu` matches your user in pos.service |
