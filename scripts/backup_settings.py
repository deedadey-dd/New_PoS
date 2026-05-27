import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')
django.setup()

from django.db import connection

def backup():
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT tenant_id, provider, is_active, public_key, secret_key, webhook_secret, callback_url, test_mode FROM payments_paymentprovidersettings')
            rows = cursor.fetchall()
            
        data = []
        for row in rows:
            data.append({
                'tenant_id': row[0],
                'provider': row[1],
                'is_active': row[2],
                'public_key': row[3],
                'secret_key': row[4],
                'webhook_secret': row[5],
                'callback_url': row[6],
                'test_mode': row[7]
            })
            
        with open('payment_settings_backup.json', 'w') as f:
            json.dump(data, f)
        print(f"Backed up {len(data)} settings.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    backup()
