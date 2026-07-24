import os

dirs = [
    'apps/sales',
    'apps/core',
    'apps/audit',
    'apps/accounting',
    'apps/sync'
]

files_to_check = []
for d in dirs:
    for root, _, files in os.walk(d):
        for f in files:
            if f.endswith('.py'):
                files_to_check.append(os.path.join(root, f))

for file_path in files_to_check:
    if 'superadmin_views.py' in file_path or 'subscriptions' in file_path:
        continue
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "status='COMPLETED'" in content or "sale__status='COMPLETED'" in content:
        if 'context_processors.py' in file_path:
            # context_processors has ECashWithdrawal status='COMPLETED' which should NOT be touched.
            new_content = content.replace("sale__status='COMPLETED'", "sale__status__in=['COMPLETED', 'PENDING_DISPATCH']")
        else:
            new_content = content.replace("status='COMPLETED'", "status__in=['COMPLETED', 'PENDING_DISPATCH']")
            new_content = new_content.replace("sale__status='COMPLETED'", "sale__status__in=['COMPLETED', 'PENDING_DISPATCH']")
            
        if new_content != content:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f'Updated {file_path}')

print("Done.")
