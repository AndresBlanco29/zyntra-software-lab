import json
import uuid
from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.ai_assistant.models import AssistantConfiguration, AssistantKnowledgeDocument, AssistantMessage, AssistantPendingAction
from config.ai_assistant.services.knowledge import rebuild_document_chunks, search_published_knowledge
from config.usuarios.models import Usuario


class AssistantApiTests(TestCase):
    def setUp(self):
        self.config = AssistantConfiguration.get_solo()
        self.config.enabled = True
        self.config.save(update_fields=['enabled'])

    def test_context_creates_anonymous_visitor_and_welcome(self):
        response = self.client.get(reverse('ai_assistant_context'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['assistant_name'], 'Paco')
        self.assertTrue(response.json()['enabled'])

    def test_conversation_message_uses_safe_fallback_without_openai_key(self):
        created = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'home', 'language': 'es'}),
            content_type='application/json',
        )
        response = self.client.post(
            reverse('ai_assistant_conversation_message', args=[created.json()['conversation_id']]),
            data=json.dumps({'message': 'Quiero registrarme'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('Paco', response.json()['message'])
        self.assertEqual(response.json()['tour_id'], 'registration')

    def test_other_visitor_cannot_access_conversation(self):
        created = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'home'}),
            content_type='application/json',
        )
        other_client = self.client_class()
        response = other_client.post(
            reverse('ai_assistant_conversation_message', args=[created.json()['conversation_id']]),
            data=json.dumps({'message': 'hello'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)

    def test_action_confirmation_cannot_be_reused_by_another_visitor(self):
        self.client.get(reverse('ai_assistant_context'))
        visitor_id = self.client.session['ai_assistant_visitor_id']
        action = AssistantPendingAction.objects.create(
            visitor_id=visitor_id,
            action_type='ADD_CART_ITEM',
            payload={'presentation_id': 999999, 'quantity': 1},
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        other_client = self.client_class()
        response = other_client.post(reverse('ai_assistant_confirm_action', args=[action.public_id]))

        self.assertEqual(response.status_code, 400)

    def test_persisted_message_redacts_email(self):
        created = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'home'}),
            content_type='application/json',
        )
        self.client.post(
            reverse('ai_assistant_conversation_message', args=[created.json()['conversation_id']]),
            data=json.dumps({'message': 'Mi correo es customer@example.com'}),
            content_type='application/json',
        )
        stored = AssistantMessage.objects.filter(role='user').latest('created_at')

        self.assertNotIn('customer@example.com', stored.content)
        self.assertIn('[EMAIL]', stored.content)


class AssistantKnowledgeTests(TestCase):
    def test_published_document_is_retrievable(self):
        document = AssistantKnowledgeDocument.objects.create(
            title='Registro de clientes',
            slug='registro-clientes',
            content='Para registrarte debes completar el formulario y cargar tu certificado.',
            status=AssistantKnowledgeDocument.STATUS_PUBLISHED,
            language='es',
        )
        rebuild_document_chunks(document)

        results = search_published_knowledge('¿Cómo completo el registro?', language='es')

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['title'], 'Registro de clientes')
