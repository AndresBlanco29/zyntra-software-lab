from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from config.ai_assistant.models import AssistantConfiguration, AssistantConversation, AssistantPendingAction


class Command(BaseCommand):
    help = 'Delete expired assistant conversations and expire pending actions.'

    def handle(self, *args, **options):
        config = AssistantConfiguration.get_solo()
        cutoff = timezone.now() - timedelta(days=config.retention_days)
        conversations, _ = AssistantConversation.objects.filter(last_activity_at__lt=cutoff).delete()
        expired = AssistantPendingAction.objects.filter(
            status=AssistantPendingAction.STATUS_PENDING,
            expires_at__lte=timezone.now(),
        ).update(status=AssistantPendingAction.STATUS_EXPIRED)
        self.stdout.write(self.style.SUCCESS(
            f'Removed {conversations} expired conversation records; expired {expired} pending actions.'
        ))
