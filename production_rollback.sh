#!/bin/bash
# Production Rollback Script for Alpha Changes
# IMPORTANT: Run this script while you are STILL on the branch/commit 
# that contains the alpha migrations (before you switch back to main).

set -e

echo "=================================================="
echo " Starting Production Rollback of Alpha Features"
echo "=================================================="

echo ""
echo "[Step 1] Fixing NOT NULL database constraints..."
echo "Running database fixes via Django shell..."

cat << 'EOF' | python manage.py shell
from django.db import connection

with connection.cursor() as cursor:
    # 1. Fix CashTransfer.to_user
    try:
        cursor.execute("UPDATE accounting_cashtransfer SET to_user_id = from_user_id WHERE to_user_id IS NULL;")
        print(" -> Fixed accounting_cashtransfer NULL rows.")
    except Exception as e:
        print(f" -> Skipped accounting_cashtransfer fix (already clean or error: {e})")
        
    # 2. Fix Sale.is_dispatched
    try:
        # Postgres uses boolean False, SQLite uses 0
        try:
            cursor.execute("UPDATE sales_sale SET is_dispatched = False WHERE is_dispatched IS NULL;")
        except Exception:
            cursor.execute("UPDATE sales_sale SET is_dispatched = 0 WHERE is_dispatched IS NULL;")
        print(" -> Fixed sales_sale.is_dispatched NULL rows.")
    except Exception as e:
        pass
        
    # 3. Fix Sale.is_disputed
    try:
        try:
            cursor.execute("UPDATE sales_sale SET is_disputed = False WHERE is_disputed IS NULL;")
        except Exception:
            cursor.execute("UPDATE sales_sale SET is_disputed = 0 WHERE is_disputed IS NULL;")
        print(" -> Fixed sales_sale.is_disputed NULL rows.")
    except Exception as e:
        pass
EOF

echo ""
echo "[Step 2] Rolling back application migrations to main state..."
python manage.py migrate sales 0007
python manage.py migrate payments 0002
python manage.py migrate inventory 0006
python manage.py migrate accounting 0001
python manage.py migrate core 0011

echo ""
echo "=================================================="
echo "✅ Rollback complete!"
echo "=================================================="
echo "You can now safely run:"
echo "  git checkout main"
echo "  # Restart your WSGI/ASGI or Gunicorn server here"
