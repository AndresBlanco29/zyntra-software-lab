import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config.ai_assistant.models import (
    AssistantConfiguration,
    AssistantKnowledgeDocument,
    AssistantMessage,
    AssistantPendingAction,
    AssistantVisitorProfile,
)
from config.ai_assistant.services.knowledge import _cosine_similarity, rebuild_document_chunks, search_published_knowledge
from config.ai_assistant.services.openai_client import OpenAIClient
from config.ai_assistant.services.orchestrator import _authorized_tour_for_message, _guided_actions
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
        self.assertEqual(response.json()['proactive']['kind'], 'first_visit')
        self.assertTrue(AssistantVisitorProfile.objects.exists())
        self.assertIn('ai_assistant_visitor', response.cookies)

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

    def test_cosine_similarity_ranks_matching_vectors(self):
        self.assertGreater(_cosine_similarity([1, 0, 0], [0.9, 0.1, 0]), 0.9)
        self.assertEqual(_cosine_similarity([1, 0], [0, 1]), 0)


class OpenAIClientTests(TestCase):
    @patch.object(OpenAIClient, '_post')
    def test_create_response_extracts_raw_responses_api_message_text(self, mock_post):
        mock_post.return_value = {
            'id': 'resp_test',
            'output': [{
                'type': 'message',
                'content': [{
                    'type': 'output_text',
                    'text': 'Conexión correcta.',
                }],
            }],
            'usage': {'input_tokens': 1, 'output_tokens': 2},
        }

        response = OpenAIClient().create_response(
            model='gpt-4.1-mini',
            instructions='Test',
            input_messages=[{'role': 'user', 'content': 'Hola'}],
            tools=[],
        )

        self.assertEqual(response['text'], 'Conexión correcta.')


class GuidedTourIntentTests(TestCase):
    def test_registration_request_gets_only_the_authorized_registration_tour(self):
        context = {'authenticated': False, 'next_recommended_action': {'label': 'Registrarme', 'url': '/registro/'}}

        tour_id = _authorized_tour_for_message('Ayúdame a registrarme', context)
        actions = _guided_actions(context, tour_id)

        self.assertEqual(tour_id, 'registration')
        self.assertEqual(actions[0]['tour_id'], 'registration')
        self.assertIn('ai_tour=registration', actions[0]['url'])

    def test_affirmative_registration_answer_starts_registration_tour(self):
        self.assertEqual(
            _authorized_tour_for_message('Sí', {'authenticated': False}),
            'registration',
        )

    def test_login_and_recovery_intents_only_return_ui_tours(self):
        context = {'authenticated': False}
        self.assertEqual(_authorized_tour_for_message('Quiero iniciar sesión', context), 'login')
        self.assertEqual(_authorized_tour_for_message('Olvidé mi contraseña', context), 'password-recovery')
        self.assertEqual(_guided_actions(context, 'login')[0]['tour_id'], 'login')


class VerificationTests(TestCase):
    def test_invalid_or_reused_otp_cannot_verify_status(self):
        from config.ai_assistant.services.verification import issue_account_status_challenge, verify_account_status_challenge

        challenge = issue_account_status_challenge('nobody@example.com')
        self.assertIsNone(verify_account_status_challenge(challenge.public_id, '000000'))
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 1)

    @patch('config.ai_assistant.services.verification.send_mail', return_value=1)
    @patch('config.ai_assistant.services.verification.secrets.randbelow', return_value=123456)
    def test_correct_otp_is_accepted_once_for_the_registered_customer(self, _random_code, send_mail_mock):
        from config.ai_assistant.services.verification import issue_account_status_challenge, verify_account_status_challenge
        from config.clientes.models import Cliente

        user = Usuario.objects.create_user(
            username='otp-customer',
            email='otp-customer@example.com',
            password='safe-password',
            role='cliente',
        )
        cliente = Cliente.objects.create(
            usuario=user,
            nombre_empresa='OTP Test',
            telefono='5551234567',
            direccion='123 Test Street',
            ciudad='Atlanta',
            estado='GA',
            sales_tax_number='OTP-1',
            certificado_tax='certificados/test.pdf',
        )
        # The OTP resolves ownership through Cliente, not a mutable user role.
        user.role = 'backoffice'
        user.save(update_fields=['role'])

        challenge = issue_account_status_challenge(user.email)
        self.assertEqual(verify_account_status_challenge(challenge.public_id, '123456'), cliente)
        self.assertIsNone(verify_account_status_challenge(challenge.public_id, '123456'))
        send_mail_mock.assert_called_once()
