import os
import sys
import django
import traceback
import pprint

# Ensure project root is on sys.path so Django can import `config` settings
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')
django.setup()

from config.integrations.models import QuickBooksImportConflict
from config.integrations.quickbooks.sync import retry_quickbooks_import_conflict

conflict = QuickBooksImportConflict.objects.filter(pk=49).first()
print('conflict', conflict and conflict.id)
try:
    res = retry_quickbooks_import_conflict(conflict)
    pprint.pprint(res)
except Exception:
    traceback.print_exc()
