import os, sys, django
ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.config.settings')

django.setup()
from config.integrations.quickbooks.sync import import_quickbooks_items
import pprint

pp = pprint.PrettyPrinter(indent=2)
print('Running import_quickbooks_items(max_results=500)')
res = import_quickbooks_items(max_results=500)
pp.pprint(res)
