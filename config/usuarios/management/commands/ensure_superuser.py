import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create a superuser from environment variables if it does not exist."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

        if not username or not password:
            self.stdout.write(self.style.WARNING(
                "Skipping superuser creation because DJANGO_SUPERUSER_USERNAME or DJANGO_SUPERUSER_PASSWORD is missing."
            ))
            return

        user_model = get_user_model()
        user = user_model.objects.filter(username=username).first()

        if user:
            updated_fields = []
            if email and user.email != email:
                user.email = email
                updated_fields.append("email")
            if not user.is_staff:
                user.is_staff = True
                updated_fields.append("is_staff")
            if not user.is_superuser:
                user.is_superuser = True
                updated_fields.append("is_superuser")
            if getattr(user, "role", None) != "admin":
                user.role = "admin"
                updated_fields.append("role")

            if updated_fields:
                user.save(update_fields=updated_fields)
                self.stdout.write(self.style.SUCCESS(
                    f"Updated existing superuser '{username}'."
                ))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"Superuser '{username}' already exists."
                ))
            return

        user_model.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            role="admin",
        )
        self.stdout.write(self.style.SUCCESS(
            f"Superuser '{username}' created successfully."
        ))