import os
import django
from django.test import Client
from django.contrib.auth.models import User

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

def validate_qb_hub():
    client = Client()
    # Ensure we have an admin user
    try:
        admin = User.objects.filter(is_superuser=True).first()
        if not admin:
            admin = User.objects.create_superuser('temp_admin', 'admin@example.com', 'password')
            cleanup_admin = True
        else:
            cleanup_admin = False
            
        client.force_login(admin)
        response = client.get('/quickbooks/')
        
        print(f"Status Code: {response.status_code}")
        content = response.content.decode('utf-8')
        
        check1 = "Today everything looks under control" in content or "QuickBooks is not connected yet" in content
        check2 = "Open review queue" in content
        
        print(f"Content Check 1 (Status Message): {check1}")
        print(f"Content Check 2 (Review Queue): {check2}")
        
    except Exception as e:
        print(f"Error validating QB hub: {e}")

if __name__ == "__main__":
    validate_qb_hub()
