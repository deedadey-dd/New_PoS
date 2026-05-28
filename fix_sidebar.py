with open('templates/base.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Add id to the sidebar nav
content = content.replace('<ul class="nav flex-column">', '<ul class="nav flex-column" id="sidebarAccordion">', 1)

# List of top-level menus to add data-bs-parent to
menus = [
    'invoicesMenu', 'salesMenu', 'accountingMenu', 
    'managerExpenditureMenu', 'inventoryMenu', 'settingsMenu', 
    'subscriptionMenu', 'auditMenu', 'auditorInventoryMenu', 
    'auditorExpenditureMenu'
]

for menu in menus:
    content = content.replace(f'id="{menu}"', f'data-bs-parent="#sidebarAccordion" id="{menu}"')

with open('templates/base.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Updated base.html')
