import ast
import os

scratch_path = r"C:\Users\deedadey\.gemini\antigravity-ide\brain\75630733-9d47-40e5-a76a-cf91176f5303\scratch\seed_feature_messages.py"
target_path = r"d:\PROJECTS\New_PoS\apps\core\management\commands\seed_feature_messages.py"

# SaaS keys to exclude
exclude_keys = {
    'suspend_users', 'password_reset_admin', 'forced_password_change', 
    'tenant_isolation', 'email_campaigns', 'subscription_reminders', 
    'custom_emails', 'tenant_deactivation', 'superadmin_dashboard', 
    'continuous_updates', 'contact_support'
}

# 1. Read scratch file to get the old messages list
with open(scratch_path, 'r', encoding='utf-8') as f:
    scratch_content = f.read()

# We need to extract the `messages = [...]` block securely
# Let's find the start and end of the messages list
start_idx = scratch_content.find("messages = [\n")
end_idx = scratch_content.find("]\n\ncreated_count", start_idx) + 1

messages_str = scratch_content[start_idx:end_idx]

# Safely parse the list of dicts by wrapping in an assignment and using AST, or just exec in isolated scope
local_scope = {}
exec(messages_str, {}, local_scope)
old_messages = local_scope['messages']

# Filter old messages
filtered_old_messages = [m for m in old_messages if m['feature_key'] not in exclude_keys]

# Read current target file
with open(target_path, 'r', encoding='utf-8') as f:
    target_content = f.read()

# Current target has 7 new messages, we want to append the filtered old ones
start_target = target_content.find("messages = [\n")
end_target = target_content.find("        ]\n\n        created_count", start_target) + 9

target_messages_str = target_content[start_target:end_target].replace("messages = ", "").strip()
local_scope2 = {}
exec(f"current_messages = {target_messages_str}", {}, local_scope2)
current_messages = local_scope2['current_messages']

# Combine
all_messages = current_messages + filtered_old_messages

# We also have two new ones that were in a separate script 'scripts/seed_new_feature_messages.py'
# The user wants "all the messages we have in this local branch".
# I'll just append them too if they aren't there.
extra_messages = [
    {
        'feature_key': 'combine_requests',
        'title': 'Save Time: Combine Stock Requests',
        'content': '''<h2>Fulfill Multiple Requests at Once</h2>
<p>Store Managers no longer need to fulfill branch stock requests one by one!</p>
<ul>
    <li><strong>Select Multiple:</strong> Easily check the boxes next to several pending stock requests from the same shop.</li>
    <li><strong>Merge to Transfer:</strong> Click the "Combine Selected" button to automatically generate a single, consolidated Transfer Manifest.</li>
</ul>
<p>Keep your warehouse operations lean and efficient.</p>'''
    },
    {
        'feature_key': 'change_to_account',
        'title': 'Better Customer Service: Send Change to Account',
        'content': '''<h2>Never Shortchange a Customer Again</h2>
<p>When a customer pays with a large bill and you don\\'t have the exact change, you can now seamlessly send the balance directly to their store account.</p>
<ul>
    <li><strong>Digital Wallet:</strong> The excess cash immediately credits their customer ledger.</li>
    <li><strong>Future Purchases:</strong> They can use that stored balance to pay for items the next time they visit any of your branches.</li>
</ul>
<p>Build trust and ensure your cash drawer stays perfectly balanced!</p>'''
    }
]

# Add extra if not in all_messages
existing_keys = {m['feature_key'] for m in all_messages}
for em in extra_messages:
    if em['feature_key'] not in existing_keys:
        all_messages.append(em)

# Write back to target_path
def format_dict(d):
    s = "            {\n"
    s += f"                'feature_key': '{d['feature_key']}',\n"
    title_escaped = d['title'].replace("'", "\\'")
    s += f"                'title': '{title_escaped}',\n"
    content_escaped = d['content'].replace("'''", "\\'\\'\\'")
    s += f"                'content': '''{content_escaped}'''\n"
    s += "            }"
    return s

new_messages_str = "        messages = [\n" + ",\n".join(format_dict(m) for m in all_messages) + "\n        ]"

final_content = target_content[:start_target] + new_messages_str + target_content[end_target:]

with open(target_path, 'w', encoding='utf-8') as f:
    f.write(final_content)

print(f"Merged successfully! Total messages in script: {len(all_messages)}")
