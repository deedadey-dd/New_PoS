import os
import re
from pathlib import Path

def main():
    template_dir = Path('templates')
    
    # Matches {{ obj.get_full_name|default:obj.email }} or {{ obj.first_name|default:obj.email }}
    # Also handles |truncatechars:20 if any
    pattern = re.compile(r'\{\{\s*([a-zA-Z0-9_\.]+)\.(?:get_full_name|first_name)\|default:\1\.email(?:\|[a-zA-Z0-9_:]+)*\s*\}\}')
    
    def replace_func(match):
        var_name = match.group(1)
        original = match.group(0)
        return f'{{% if {var_name} %}}{original}{{% else %}}-{{% endif %}}'
        
    count = 0
    for filepath in template_dir.rglob('*.html'):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content, n = pattern.subn(replace_func, content)
            
            if n > 0:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Updated {filepath} ({n} matches)")
                count += n
        except Exception as e:
            print(f"Error processing {filepath}: {e}")
            
    print(f"Total replacements: {count}")

if __name__ == '__main__':
    main()
