#!/usr/bin/env python
"""
Standalone Script: reset_refunded_sale.py

Run directly from server command line:
    python scripts/reset_refunded_sale.py --sale-number INV-xxxx
or:
    python scripts/reset_refunded_sale.py
"""
import os
import sys

# Setup Django environment
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pos_system.settings')

import django
django.setup()

from django.core.management import call_command

if __name__ == '__main__':
    args = sys.argv[1:]
    call_command('reset_refunded_sale', *args)

