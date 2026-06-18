"""Build config/locale/es/app_translations.json from app msgids and existing catalogs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from config.core.fill_spanish_catalog import (
    EN_PO,
    MANUAL_EN_TO_ES,
    _build_reverse_en_map,
    _looks_spanish,
    _read_po_entries,
)


ROOT = Path(__file__).resolve().parents[2]
MSGIDS = Path(__file__).with_name('app_msgids.json')
OUTPUT = ROOT / 'config' / 'locale' / 'es' / 'app_translations.json'


def build_app_translations() -> dict[str, str]:
    reverse, spanish_msgids = _build_reverse_en_map()
    msgids = json.loads(MSGIDS.read_text(encoding='utf-8'))
    translations: dict[str, str] = {}

    for msgid in msgids:
        if not msgid:
            continue
        if msgid in MANUAL_EN_TO_ES:
            translations[msgid] = MANUAL_EN_TO_ES[msgid]
        elif msgid in spanish_msgids or _looks_spanish(msgid):
            translations[msgid] = msgid
        elif msgid in reverse:
            translations[msgid] = reverse[msgid]
        elif msgid in translations:
            continue

    # Preserve any existing curated translations.
    if OUTPUT.exists():
        translations.update(json.loads(OUTPUT.read_text(encoding='utf-8')))

    return translations


if __name__ == '__main__':
    data = build_app_translations()
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
    print(f'Wrote {len(data)} app translations to {OUTPUT}')
