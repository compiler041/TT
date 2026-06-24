import re

with open('solver.py', 'r') as f:
    content = f.read()

# Comment out HC-MDM
content = re.sub(
    r'(# ── HC-MDM/OEHM: Fixed time slots ──────────────────\n.*?)(# ──)',
    r'\2',
    content,
    flags=re.DOTALL
)

with open('solver.py', 'w') as f:
    f.write(content)
