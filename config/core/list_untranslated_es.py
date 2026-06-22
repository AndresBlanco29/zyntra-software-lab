import re
from pathlib import Path

text = Path('config/locale/es/LC_MESSAGES/django.po').read_text(encoding='utf-8')
pattern = re.compile(
    r'msgid "((?:\\.|[^"\\])*)"\nmsgstr "((?:\\.|[^"\\])*)"'
)
missing = [m for m, s in pattern.findall(text) if m and not s and m[0].isupper() and len(m) < 120]
print('count', len(missing))
for s in sorted(set(missing))[:100]:
    print(s)
