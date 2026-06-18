import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
text = (ROOT / 'config/locale/es/LC_MESSAGES/django.po').read_text(encoding='utf-8')
pattern = re.compile(r'msgid "((?:\\.|[^"\\])*)"\nmsgstr "((?:\\.|[^"\\])*)"')
app_msgids = set(json.loads((Path(__file__).with_name('app_msgids.json')).read_text(encoding='utf-8')))
missing_app = [m for m, s in pattern.findall(text) if m and not s and m in app_msgids]
print('missing app strings', len(missing_app))
for s in sorted(set(missing_app))[:60]:
    print('-', s)
