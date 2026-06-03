import os, sys, django, threading, time, uuid
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')

django.setup()
from django.core.cache import cache
from config.integrations.quickbooks.sync import import_quickbooks_items

# generate task id and cache key
task_id = uuid.uuid4().hex
cache_key = f'quickbooks_task_{task_id}'
cache.set(cache_key, {'status': 'running', 'progress': 0, 'operation': 'Item'}, timeout=60*60)

print('Starting QuickBooks import in background')
print('Task id:', task_id)

# runner
def runner():
    try:
        result = import_quickbooks_items(max_results=500, task_cache_key=cache_key)
        cache.set(cache_key, {'status': 'completed', 'progress': 100, 'operation': 'Item', 'result': result}, timeout=60*60)
    except Exception as exc:
        cache.set(cache_key, {'status': 'failed', 'progress': 100, 'operation': 'Item', 'error': str(exc)}, timeout=60*60)

thread = threading.Thread(target=runner, daemon=True)
thread.start()

# monitor
last = None
while True:
    data = cache.get(cache_key) or {}
    status = data.get('status')
    progress = int(data.get('progress') or 0)
    op = data.get('operation')
    result = data.get('result')
    if data != last:
        print(f'[{time.strftime("%H:%M:%S")}] status={status} progress={progress}% op={op}')
        last = data
    if status in ('completed','failed'):
        print('Final payload:')
        print(result if result else data.get('error'))
        break
    time.sleep(1)

print('Done')
