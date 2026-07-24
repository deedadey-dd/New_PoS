import os
import re

files_to_fix = [
    'apps/core/management/commands/setup_demo.py',
    'apps/core/tests/test_recent_fixes.py',
    'apps/accounting/tests/test_digital_confirmation.py',
    'apps/accounting/tests/test_momo_history.py'
]

for file_path in files_to_fix:
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Replace occurrences of status__in=['COMPLETED', 'PENDING_DISPATCH'] back to status='COMPLETED'
        # specifically where they were mistakenly changed inside .create() calls or object instantiation.
        # Since we know exactly what string was used, a simple string replace is fine.
        new_content = content.replace("status__in=['COMPLETED', 'PENDING_DISPATCH']", "status='COMPLETED'")
        
        if new_content != content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f'Fixed {file_path}')
