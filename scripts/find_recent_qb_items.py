import os, sys, django
from datetime import datetime, timedelta
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')

django.setup()
from config.integrations.quickbooks.client import QuickBooksAPIClient
from django.utils import timezone

client = QuickBooksAPIClient()
cutoff = timezone.now() - timezone.timedelta(hours=24)
cutoff_iso = cutoff.isoformat()
print('Searching QuickBooks Items updated after', cutoff_iso)
items = client.find_updated_since('Item', cutoff_iso, max_results=500)
print('Found', len(items), 'items updated in last 24h (sample names):')
for item in items[:50]:
    print('-', item.get('Id'), item.get('Name'), ' / DisplayName=', item.get('DisplayName'))

# Also print a count of all Items
all_items = client.find_all('Item', max_results=500)
print('\nTotal items (up to 500):', len(all_items))
for item in all_items[:20]:
    print('-', item.get('Id'), item.get('Name'))
