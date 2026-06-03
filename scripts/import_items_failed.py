import os
import sys
import django
import pprint

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')
django.setup()

from config.integrations.quickbooks.sync import import_quickbooks_items

result = import_quickbooks_items(max_results=1000)
pp = pprint.PrettyPrinter(indent=2)
failed = [r for r in result.get('results', []) if not r.get('ok')]
print('\nFAILED ITEMS COUNT:', len(failed))
pp.pprint(failed)
