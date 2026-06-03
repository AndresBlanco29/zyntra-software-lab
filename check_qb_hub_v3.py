import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.test import Client
from django.contrib.auth.models import User

def validate_qb_hub():
    client = Client()
    try:
        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            print("No admin user found")
            return
            
        client.force_login(admin)
        response = client.get('/quickbooks/')
        
        print(f"Status Code: {response.status_code}")
        content = response.content.decode('utf-8')
        
        check1 = "Today everything looks under control" in content or "QuickBooks is not connected yet" in content
        check2 = "Open review queue" in content
        
        print(f"Content Check 1: {check1}")
        print(f"Content Check 2: {check2}")
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    validate_qb_hub()
