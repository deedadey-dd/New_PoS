import os
import re

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    new_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        match = re.match(r'^(\s*)def dispatch\(self,\s*request,\s*\*args,\s*\*\*kwargs\):', line)
        if match:
            dispatch_indent = match.group(1)
            
            # Look ahead to see if it needs fix
            needs_fix = False
            for j in range(i+1, min(i+20, len(lines))):
                if re.match(r'^\s*def ', lines[j]) or re.match(r'^\s*class ', lines[j]):
                    break
                if 'request.user.is_authenticated' in lines[j]:
                    break
                if 'request.user.role' in lines[j] or 'user.role' in lines[j]:
                    needs_fix = True
            
            new_lines.append(line)
            if needs_fix:
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if next_line.strip().startswith('\"\"\"') or next_line.strip().startswith("'''"):
                        new_lines.append(next_line)
                        if next_line.strip().count('\"\"\"') == 1 or next_line.strip().count("'''") == 1:
                            # Multi-line docstring
                            i += 1
                            while i < len(lines):
                                doc_line = lines[i]
                                new_lines.append(doc_line)
                                if '\"\"\"' in doc_line or "'''" in doc_line:
                                    break
                                i += 1
                        i += 1
                        continue
                    break
                
                # Insert the check
                body_indent = dispatch_indent + '    '
                new_lines.append(f'{body_indent}if not request.user.is_authenticated:')
                new_lines.append(f'{body_indent}    return super().dispatch(request, *args, **kwargs)')
                
                continue
                
        new_lines.append(line)
        i += 1
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('\n'.join(new_lines))

for root, dirs, files in os.walk('d:/PROJECTS/New_PoS/apps'):
    for file in files:
        if file.endswith('.py'):
            fix_file(os.path.join(root, file))
print('Done.')
