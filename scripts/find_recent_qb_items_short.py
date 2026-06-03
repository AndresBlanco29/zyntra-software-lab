import os, sys, django
from datetime import timedelta
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')

django.setup()
from config.integrations.quickbooks.client import QuickBooksAPIClient
from django.utils import timezone

client = QuickBooksAPIClient()
cutoff = timezone.now() - timezone.timedelta(minutes=60)
cutoff_iso = cutoff.isoformat()
print('Searching QuickBooks Items updated after', cutoff_iso)
items = client.find_updated_since('Item', cutoff_iso, max_results=500)
print('Found', len(items), 'items updated in last 60 minutes:')
for item in items:
    print('-', item.get('Id'), repr(item.get('Name')), 'Sku=', item.get('Sku'))
