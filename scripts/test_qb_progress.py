import os, sys, django, time
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')

django.setup()
from django.core.cache import cache
from config.integrations.quickbooks.sync import import_quickbooks_items

key = 'quickbooks_task_test123'
print('Starting import with task key', key)
res = import_quickbooks_items(max_results=20, task_cache_key=key)
print('Import finished, result summary:')
print(res)
print('Cache entry:')
print(cache.get(key))
# emulate completion
cache.set(key, {'status':'completed','progress':100,'operation':'Item','result':res}, timeout=3600)
print('Cache after completion:')
print(cache.get(key))
