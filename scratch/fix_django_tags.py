import os
import re

template_dir = r"d:\PROJECTS\New_PoS\templates"

# Match {{ ... }} and {% ... %} across multiple lines
pattern_vars = re.compile(r'\{\{(.+?)\}\}', re.DOTALL)
pattern_tags = re.compile(r'\{%(.+?)%\}', re.DOTALL)

def fix_newlines(match):
    # Get the inner content
    inner = match.group(1)
    
    # If there's no newline in the inner content, we don't need to fix anything
    if '\n' not in inner:
        return match.group(0)
        
    # Replace newlines with spaces and collapse multiple spaces
    inner_fixed = re.sub(r'\s+', ' ', inner)
    
    # Check if it was a variable {{ }} or a tag {% %}
    if match.group(0).startswith('{{'):
        return '{{' + inner_fixed + '}}'
    else:
        return '{%' + inner_fixed + '%}'

fixed_count = 0
for root, dirs, files in os.walk(template_dir):
    for f in files:
        if f.endswith('.html'):
            filepath = os.path.join(root, f)
            with open(filepath, 'r', encoding='utf-8') as file:
                content = file.read()
            
            # Find and replace
            new_content = pattern_vars.sub(fix_newlines, content)
            new_content = pattern_tags.sub(fix_newlines, new_content)
            
            if new_content != content:
                with open(filepath, 'w', encoding='utf-8') as file:
                    file.write(new_content)
                print(f"Fixed: {filepath}")
                fixed_count += 1

print(f"\nDone! Fixed Django template syntax in {fixed_count} files.")
