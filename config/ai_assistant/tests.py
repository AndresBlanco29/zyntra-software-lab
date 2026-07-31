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
        user.email = ' otp-customer@example.com '
        user.save(update_fields=['role', 'email'])

        challenge = issue_account_status_challenge('otp-customer@example.com')
        self.assertEqual(verify_account_status_challenge(challenge.public_id, '123456'), cliente)
        self.assertIsNone(verify_account_status_challenge(challenge.public_id, '123456'))
        send_mail_mock.assert_called_once()


class CommercialAssistantTests(TestCase):
    def test_contact_dto_uses_backoffice_configuration(self):
        from config.ai_assistant.services.contact import build_contact_dto

        config = AssistantConfiguration.get_solo()
        config.support_phone = '+1 (404) 555-0100'
        config.support_whatsapp = '14045550100'
        config.support_email = 'support@example.com'
        config.save(update_fields=['support_phone', 'support_whatsapp', 'support_email'])

        dto = build_contact_dto()

        self.assertEqual(dto['email'], 'support@example.com')
        self.assertIn('tel:+14045550100', [action['url'] for action in dto['actions']])
        self.assertIn('https://wa.me/14045550100', [action['url'] for action in dto['actions']])

    def test_catalog_resolver_handles_normalized_partial_product_name(self):
        from config.ai_assistant.services.catalog_resolver import find_products
        from config.productos.models import Producto

        Producto.objects.create(nombre='Coca-Cola Original', activo=True)

        result = find_products('coca cola')

        self.assertEqual(result['products'][0]['name'], 'Coca-Cola Original')

    def test_active_promotion_cards_use_real_active_promotions(self):
        from config.ai_assistant.services.promotion_catalog import active_promotion_cards
        from config.productos.models import Producto, Promocion, PromocionEscala

        product = Producto.objects.create(nombre='Monster Energy 24 OZ', activo=True)
        promotion = Promocion.objects.create(
            nombre='Monster por volumen',
            descripcion='Precio especial por caja',
            producto=product,
            activa=True,
        )
        PromocionEscala.objects.create(
            promocion=promotion,
            cantidad_minima=10,
            tipo_beneficio=PromocionEscala.TIPO_FREE_UNITS,
            unidades_gratis=1,
        )

        result = active_promotion_cards(related_product_id=product.id)

        self.assertTrue(result['related'])
        self.assertEqual(result['cards'][0]['product_name'], 'Monster Energy 24 OZ')
        self.assertIn('Compra 10+', result['cards'][0]['benefits'][0])


class CustomerSuccessProfileTests(TestCase):
    def test_profile_remembers_recent_product_without_conversation_content(self):
        from config.ai_assistant.services.customer_success_profile import touch_success_profile
        from config.clientes.models import Cliente

        user = Usuario.objects.create_user(username='success-customer', password='safe-password', role='cliente')
        cliente = Cliente.objects.create(
            usuario=user,
            nombre_empresa='Success Test',
            telefono='5551234567',
            direccion='123 Test Street',
            ciudad='Atlanta',
            estado='GA',
            sales_tax_number='SUCCESS-1',
            certificado_tax='certificados/test.pdf',
        )

        profile = touch_success_profile(
            cliente=cliente,
            module='catalog',
            product={'id': 42, 'name': 'Producto de prueba'},
            help_topic='product-search',
        )

        self.assertEqual(profile.last_module, 'catalog')
        self.assertEqual(profile.recently_viewed_products[0]['id'], 42)
        self.assertEqual(profile.help_topics[0], 'product-search')


class ConversationPurchaseTests(TestCase):
    def test_ordinal_reference_uses_saved_catalog_results(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.conversation_purchase import resolve_catalog_reference, save_catalog_results

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')
        save_catalog_results(conversation, [
            {
                'product_id': 10,
                'name': 'SODA COCA COLA 6/3LT',
                'presentations': [{'id': 99, 'name': 'CS'}],
                'score': 0.99,
            },
            {
                'product_id': 11,
                'name': 'SODA COCA COLA 24/20OZ',
                'presentations': [{'id': 100, 'name': 'CS'}],
                'score': 0.88,
            },
        ])

        reference = resolve_catalog_reference(conversation, 'Necesito 10 del primero CS')

        self.assertEqual(reference['product']['product_id'], 10)
        self.assertEqual(reference['presentation']['id'], 99)
        self.assertEqual(reference['quantity'], 10)


class AgentContextTests(TestCase):
    def _conversation(self):
        from config.ai_assistant.models import AssistantConversation

        return AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')

    def _with_selected_product(self, conversation):
        from config.ai_assistant.services.conversation_purchase import save_catalog_results

        save_catalog_results(conversation, [{
            'product_id': 55,
            'name': 'SODA COCA COLA 6/3LT',
            'presentations': [{'id': 77, 'name': 'CS'}],
            'score': 0.97,
        }])
        return conversation

    def test_quantity_reply_keeps_current_product_instead_of_account_answer(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        conversation = self._with_selected_product(self._conversation())

        intent = resolve_intent(
            conversation=conversation,
            message='Necesito 10 cajas de ese producto',
            context={'authenticated': True},
        )

        self.assertEqual(intent, 'product_reference')

    def test_invoice_question_still_routes_to_customer_success(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        intent = resolve_intent(
            conversation=self._conversation(),
            message='¿Cuánto debo en mis facturas?',
            context={'authenticated': True},
        )

        self.assertEqual(intent, 'customer_success')

    def test_promotion_question_routes_to_promotions(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        intent = resolve_intent(
            conversation=self._conversation(),
            message='Hay promociones?',
            context={'authenticated': False},
        )

        self.assertEqual(intent, 'promotions')

    def test_deictic_reference_resolves_to_selected_product(self):
        from config.ai_assistant.services.conversation_purchase import resolve_catalog_reference

        conversation = self._with_selected_product(self._conversation())

        reference = resolve_catalog_reference(conversation, 'Quiero 10 de ese producto')

        self.assertEqual(reference['product']['product_id'], 55)
        self.assertEqual(reference['quantity'], 10)

    def test_numeric_pick_after_ambiguous_list_resolves_that_option(self):
        from config.ai_assistant.services.conversation_purchase import (
            resolve_catalog_reference,
            save_catalog_results,
        )

        conversation = self._conversation()
        save_catalog_results(conversation, [
            {'product_id': 1, 'name': 'SODA COCA COLA 6/3LT', 'presentations': []},
            {'product_id': 2, 'name': 'SODA COCA COLA 24/12OZ', 'presentations': []},
        ])

        reference = resolve_catalog_reference(conversation, 'el 2')

        self.assertEqual(reference['product']['product_id'], 2)

    def test_state_expires_without_leaking_previous_product(self):
        from config.ai_assistant.services.conversation_state import load_state, update_state

        conversation = self._with_selected_product(self._conversation())
        stale = dict(conversation.shopping_context)
        stale['expires_at'] = (timezone.now() - timedelta(minutes=1)).isoformat()
        conversation.shopping_context = stale
        conversation.save(update_fields=['shopping_context'])

        self.assertIsNone(load_state(conversation)['selected_product'])
        update_state(conversation, last_intent='product_search')
        self.assertEqual(load_state(conversation)['last_intent'], 'product_search')


class ConversationContinuityTests(TestCase):
    def setUp(self):
        config = AssistantConfiguration.get_solo()
        config.enabled = True
        config.save(update_fields=['enabled'])
        self.user = Usuario.objects.create_user(
            username='thread-customer',
            password='secret123',
            role='cliente',
            first_name='Andres Felipe',
        )
        self.client.force_login(self.user)

    def test_reopening_the_chat_on_another_page_returns_the_same_thread(self):
        from config.ai_assistant.models import AssistantConversation

        first = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'catalog'}),
            content_type='application/json',
        ).json()
        conversation = AssistantConversation.objects.get(public_id=first['conversation_id'])
        AssistantMessage.objects.create(
            conversation=conversation,
            role=AssistantMessage.ROLE_USER,
            content='necesito coca cola',
        )
        AssistantMessage.objects.create(
            conversation=conversation,
            role=AssistantMessage.ROLE_ASSISTANT,
            content='Encontré SODA COCA COLA 6/3LT.',
        )

        resumed = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'cart'}),
            content_type='application/json',
        ).json()

        self.assertEqual(resumed['conversation_id'], first['conversation_id'])
        self.assertEqual(
            [item['content'] for item in resumed['messages']],
            ['necesito coca cola', 'Encontré SODA COCA COLA 6/3LT.'],
        )

    def test_customer_display_name_uses_the_first_given_name(self):
        from config.ai_assistant.services.context import customer_display_name

        self.assertEqual(customer_display_name(self.user, None), 'Andres')


class ToolPayloadSerializationTests(TestCase):
    def test_domain_values_in_a_tool_payload_can_be_stored(self):
        """A UUID, Decimal or date in a tool result used to abort the whole turn."""
        import datetime
        from decimal import Decimal

        from config.ai_assistant.models import AssistantConversation

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')
        payload = {
            'quote_public_id': uuid.uuid4(),
            'balance': Decimal('1250.75'),
            'due_date': datetime.date(2026, 8, 15),
        }

        message = AssistantMessage.objects.create(
            conversation=conversation,
            role=AssistantMessage.ROLE_TOOL,
            content='summary',
            tool_name='get_customer_success_summary',
            tool_payload=payload,
        )
        message.refresh_from_db()

        self.assertEqual(message.tool_payload['balance'], '1250.75')
        self.assertEqual(message.tool_payload['due_date'], '2026-08-15')


class CatalogNamingTests(TestCase):
    def test_catalog_answer_keeps_the_stored_product_name_verbatim(self):
        from config.ai_assistant.services.orchestrator import _exact_catalog_answer

        answer = _exact_catalog_answer([
            {'name': 'SODA COCA COLA 6/3LT', 'presentations': [{'id': 1, 'name': 'CS'}]},
            {'name': 'SODA COCA COLA MEX 24/12OZ', 'presentations': []},
        ])

        self.assertIn('SODA COCA COLA 6/3LT', answer)
        self.assertIn('SODA COCA COLA MEX 24/12OZ', answer)
        self.assertNotIn('cajas de 3 litros', answer)
        self.assertNotIn('onzas', answer)

    def test_search_results_replace_any_paraphrased_model_text(self):
        from config.ai_assistant.services.orchestrator import (
            _catalog_products_from_tools,
            _exact_catalog_answer,
        )

        tool_results = [{
            'name': 'find_products',
            'result': {'products': [{'name': 'SODA COCA COLA 6/3LT', 'presentations': []}]},
        }]

        products = _catalog_products_from_tools(tool_results)

        self.assertIsNotNone(products)
        self.assertIn('SODA COCA COLA 6/3LT', _exact_catalog_answer(products))


class ConversationOwnershipTests(TestCase):
    def setUp(self):
        config = AssistantConfiguration.get_solo()
        config.enabled = True
        config.save(update_fields=['enabled'])
        self.visitor_id = uuid.uuid4()
        self.customer_user = Usuario.objects.create_user(
            username='assistant-owner',
            password='secret123',
            role='cliente',
        )

    def _get_or_create(self, user):
        from config.ai_assistant.services.orchestrator import get_or_create_conversation

        return get_or_create_conversation(
            visitor_id=self.visitor_id,
            user=user,
            cliente=None,
            page='home',
            language='es',
        )

    def test_logged_out_visitor_gets_a_fresh_conversation_instead_of_a_dead_one(self):
        """Reusing the signed-in conversation after logout would 404 on every message."""
        from django.contrib.auth.models import AnonymousUser

        owned = self._get_or_create(self.customer_user)
        anonymous = self._get_or_create(AnonymousUser())

        self.assertNotEqual(anonymous.public_id, owned.public_id)
        self.assertIsNone(anonymous.user_id)

    def test_signed_in_customer_reuses_their_own_conversation(self):
        first = self._get_or_create(self.customer_user)
        second = self._get_or_create(self.customer_user)

        self.assertEqual(first.public_id, second.public_id)

    def test_message_endpoint_recovers_after_the_customer_logs_out(self):
        from config.ai_assistant.services.identity import VISITOR_COOKIE_NAME

        self.client.cookies[VISITOR_COOKIE_NAME] = str(self.visitor_id)
        owned = self._get_or_create(self.customer_user)

        stale = self.client.post(
            reverse('ai_assistant_conversation_message', args=[owned.public_id]),
            data=json.dumps({'message': 'hay promociones?'}),
            content_type='application/json',
        )
        self.assertEqual(stale.status_code, 404)

        created = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'home'}),
            content_type='application/json',
        )
        self.assertEqual(created.status_code, 200)
        recovered = self.client.post(
            reverse('ai_assistant_conversation_message', args=[created.json()['conversation_id']]),
            data=json.dumps({'message': 'hay promociones?'}),
            content_type='application/json',
        )
        self.assertNotEqual(recovered.status_code, 404)


class ToolRuntimeTests(TestCase):
    def test_failing_tool_returns_safe_error_without_raising(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.tool_runtime import run_tool, tool_failed

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')
        with patch(
            'config.ai_assistant.services.tool_runtime.execute_tool',
            side_effect=RuntimeError('database is down'),
        ):
            result = run_tool(request=None, conversation=conversation, name='find_products')

        self.assertTrue(tool_failed(result))
        self.assertEqual(conversation.messages.filter(tool_name='find_products').count(), 1)

    def test_unavailable_result_never_exposes_internal_details(self):
        from config.ai_assistant.services.tool_runtime import unavailable_result

        message = unavailable_result()['message']

        self.assertNotIn('error', message.lower())
        self.assertIn('intentarlo nuevamente', message)
