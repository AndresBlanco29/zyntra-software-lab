from django.core.management.base import BaseCommand

from config.ai_assistant.models import AssistantConfiguration, AssistantKnowledgeDocument
from config.ai_assistant.services.knowledge import rebuild_document_chunks


class Command(BaseCommand):
    help = 'Create or refresh the operational contact and location knowledge used by the AI Assistant.'

    def handle(self, *args, **options):
        config = AssistantConfiguration.get_solo()
        documents = {
            'contacto-y-soporte': (
                'Contacto y soporte',
                (
                    f'Para contactar La Tortilla Grocery use teléfono {config.support_phone}, '
                    f'WhatsApp {config.support_whatsapp} o correo {config.support_email}. '
                    'Si una solicitud no puede resolverse, ofrecer contacto con un asesor.'
                ),
            ),
            'ubicacion-y-rutas': (
                'Ubicación y cobertura de rutas',
                (
                    f'La Tortilla Grocery cuenta con una ubicación física: {config.location_address or "consultar con un asesor"}. '
                    f'Las rutas directas actualmente cubren {config.delivery_coverage}.'
                ),
            ),
        }
        for slug, (title, content) in documents.items():
            document, _ = AssistantKnowledgeDocument.objects.update_or_create(
                slug=slug,
                defaults={
                    'title': title,
                    'content': content,
                    'category': 'commercial-support',
                    'language': 'es',
                    'status': AssistantKnowledgeDocument.STATUS_PUBLISHED,
                },
            )
            rebuild_document_chunks(document)
        self.stdout.write(self.style.SUCCESS('Commercial assistant knowledge refreshed.'))
