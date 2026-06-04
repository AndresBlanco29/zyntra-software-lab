#!/usr/bin/env python
import os, sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.config.settings')
import django
try:
    django.setup()
except Exception as e:
    print('Django setup error', e); sys.exit(2)
from django.template.loader import get_template
try:
    t = get_template('backoffice/quickbooks_hub.html')
    print('FOUND', t.template.name if hasattr(t,'template') else repr(t))
except Exception as e:
    print('ERROR', type(e).__name__, e)
    import traceback; traceback.print_exc()

# Diagnostic: print TEMPLATE_DIRS and check for file existence
from django.conf import settings
print('TEMPLATE DIRS:', settings.TEMPLATES[0].get('DIRS'))
import os
for d in settings.TEMPLATES[0].get('DIRS'):
    p = os.path.join(str(d), 'backoffice', 'quickbooks_hub.html')
    print(p, os.path.exists(p))
sys.exit(1)

# Diagnostic: print TEMPLATE_DIRS and check for file existence
from django.conf import settings
print('TEMPLATE DIRS:', settings.TEMPLATES[0].get('DIRS'))
import os
for d in settings.TEMPLATES[0].get('DIRS'):
     p = os.path.join(str(d), 'backoffice', 'quickbooks_hub.html')
     print(p, os.path.exists(p))
