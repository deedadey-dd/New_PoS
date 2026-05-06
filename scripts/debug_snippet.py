path = r'd:\PROJECTS\New_PoS\apps\core\views.py'
with open(path, 'r', newline='') as f:
    content = f.read()

needle = 'today_dispatched_value'
idx = content.find(needle)
print(repr(content[idx:idx+150]))
