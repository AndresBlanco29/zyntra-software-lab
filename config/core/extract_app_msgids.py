import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config'
trans: set[str] = set()
for path in CONFIG.rglob('*'):
    if path.suffix not in {'.html', '.py'}:
        continue
    if 'locale' in path.parts or 'staticfiles' in path.parts:
        continue
    text = path.read_text(encoding='utf-8', errors='ignore')
    trans.update(re.findall(r'(?:trans|blocktrans)\s+"([^"]+)"', text))
    trans.update(re.findall(r"_\(\s*['\"]([^'\"]+)['\"]\s*\)", text))
    trans.update(re.findall(r"gettext\(\s*['\"]([^'\"]+)['\"]\s*\)", text))

Path(__file__).with_name('app_msgids.json').write_text(
    json.dumps(sorted(trans), ensure_ascii=False, indent=2),
    encoding='utf-8',
)
print('unique strings', len(trans))
