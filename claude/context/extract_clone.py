#!/usr/bin/env python3
# Helper: extract clone commands from www/index.html
import re, html as html_module

with open('/home/maria/claude/claude-dashboard/www/index.html') as f:
    content = f.read()

# Find all clone-btn sections by finding the data attribute
# The attribute is in form: data-clone-cmds="..." (unescaped in HTML)
pattern = r'class="[^"]*clone-btn[^"]*"[^>]*data-clone-cmds="([^"]*)"'
matches = re.findall(pattern, content)
for i, m in enumerate(matches):
    print(f'=== PROJECT {i+1} ===')
    print(html_module.unescape(m))
    print()
