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
from config.integrations.models import QuickBooksImportConflict

# adjust max_results as needed; QuickBooks sandbox may have many items
result = import_quickbooks_items(max_results=1000)
pp = pprint.PrettyPrinter(indent=2)
print('\nIMPORT SUMMARY:')
pp.pprint({k: result.get(k) for k in ('entity','count','created_count','updated_count','conflict_count','failed_count')})
print('\nSample results (last 10):')
pp.pprint(result.get('results', [])[-10:])

print('\nRecent QuickBooksImportConflict rows (10):')
conflicts = QuickBooksImportConflict.objects.order_by('-id')[:10]
for c in conflicts:
    print(c.id, c.entity_type, c.quickbooks_id, c.display_name, c.reason[:120])
