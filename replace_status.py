import re

file_path = 'apps/accounting/views.py'
with open(file_path, 'r') as f:
    content = f.read()

new_content = content.replace("status='COMPLETED'", "status__in=['COMPLETED', 'PENDING_DISPATCH']")

with open(file_path, 'w') as f:
    f.write(new_content)
print("Replacement complete.")
