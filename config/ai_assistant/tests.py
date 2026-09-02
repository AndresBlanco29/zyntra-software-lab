import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings
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

    def test_first_visit_greeting_uses_the_configured_assistant_name(self):
        self.config.assistant_name = 'Isabella'
        self.config.save(update_fields=['assistant_name'])

        response = self.client.get(reverse('ai_assistant_context'))

        message = response.json()['proactive']['message']
        self.assertIn('Soy Isabella', message)
        self.assertNotIn('Paco', message)

    def test_first_visit_greeting_can_be_requested_in_english(self):
        self.config.assistant_name = 'Isabella'
        self.config.save(update_fields=['assistant_name'])

        response = self.client.get(reverse('ai_assistant_context'), {'language': 'en'})

        payload = response.json()
        message = payload['proactive']['message']
        self.assertEqual(payload['language'], 'en')
        self.assertIn("I'm Isabella", message)
        self.assertIn('La Tortilla Grocery LLC', message)
        self.assertIn('Register as a customer', payload['proactive']['actions'][0]['label'])

    def test_create_conversation_updates_language_on_existing_thread(self):
        created = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'home', 'language': 'es'}),
            content_type='application/json',
        )
        conversation_id = created.json()['conversation_id']
        updated = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'home', 'language': 'en'}),
            content_type='application/json',
        )
        self.assertEqual(updated.json()['conversation_id'], conversation_id)
        from config.ai_assistant.models import AssistantConversation
        conversation = AssistantConversation.objects.get(public_id=conversation_id)
        self.assertEqual(conversation.language, 'en')

    def test_first_visit_proactive_marks_auto_open(self):
        response = self.client.get(reverse('ai_assistant_context'))
        self.assertTrue(response.json()['proactive'].get('auto_open'))

    def test_dismiss_proactive_sets_quiet_window_and_hides_first_visit(self):
        self.client.get(reverse('ai_assistant_context'))
        dismiss = self.client.post(reverse('ai_assistant_dismiss_proactive'))
        self.assertEqual(dismiss.status_code, 200)
        self.assertTrue(dismiss.json()['success'])

        profile = AssistantVisitorProfile.objects.get()
        self.assertIsNotNone(profile.quiet_until)
        self.assertGreater(profile.quiet_until, timezone.now())
        # Even if the first-visit flag is cleared, quiet_until must suppress auto prompts.
        profile.first_visit_prompted_at = None
        profile.save(update_fields=['first_visit_prompted_at'])

        again = self.client.get(reverse('ai_assistant_context'))
        self.assertIsNone(again.json().get('proactive'))

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

    def test_order_question_still_routes_to_customer_success(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        intent = resolve_intent(
            conversation=self._conversation(),
            message='¿En qué estado está mi pedido?',
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


class ScopedAnswerTests(TestCase):
    def _conversation(self):
        from config.ai_assistant.models import AssistantConversation

        return AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')

    def test_billing_question_is_handed_off_instead_of_answered(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        intent = resolve_intent(
            conversation=self._conversation(),
            message='cuanto debo en mis facturas?',
            context={'authenticated': True},
        )

        self.assertEqual(intent, 'billing_handoff')

    def test_handoff_points_to_a_human_agent_on_whatsapp(self):
        from config.ai_assistant.services.orchestrator import _billing_handoff_result

        config = AssistantConfiguration.get_solo()
        config.support_whatsapp = '14045550100'
        config.save(update_fields=['support_whatsapp'])

        result = _billing_handoff_result()

        self.assertIn('facturación', result['message'])
        self.assertEqual(
            result['suggested_actions'][0]['label'],
            'Hablar con el gerente de ventas por WhatsApp',
        )
        self.assertTrue(result['suggested_actions'][0]['url'].startswith('https://wa.me/'))

    def test_billing_handoff_uses_english_when_conversation_language_is_en(self):
        from config.ai_assistant.services.orchestrator import _billing_handoff_result

        config = AssistantConfiguration.get_solo()
        config.support_whatsapp = '14045550100'
        config.save(update_fields=['support_whatsapp'])

        result = _billing_handoff_result(language='en')

        self.assertIn('Billing', result['message'])
        self.assertEqual(
            result['suggested_actions'][0]['label'],
            'Talk with sales manager on WhatsApp',
        )

    def test_module_is_detected_from_the_page_the_customer_is_on(self):
        from config.ai_assistant.services.context import current_module

        self.assertEqual(current_module('/pedidos/cliente/ordenes-recibidas/'), 'orders')
        self.assertEqual(current_module('cart'), 'cart')
        self.assertEqual(current_module('/productos/catalogo/'), 'catalog')
        self.assertEqual(current_module(''), 'home')


class GuestAccountStatusTests(TestCase):
    def test_visitor_asking_for_their_quote_is_routed_to_sign_in(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.intent_router import resolve_intent

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')

        intent = resolve_intent(
            conversation=conversation,
            message='puedo saber el estado de mi pedido',
            context={'authenticated': False},
        )

        self.assertEqual(intent, 'guest_account_status')

    def test_sign_in_invitation_never_reveals_status_and_offers_a_login_tour(self):
        from config.ai_assistant.services.orchestrator import _guest_account_status_result

        result = _guest_account_status_result({'authenticated': False})

        self.assertEqual(result['tour_id'], 'login')
        self.assertIn('Iniciar sesión', [action['label'] for action in result['suggested_actions']])
        self.assertNotIn('no pude obtener', result['message'].lower())

    def test_signed_in_customer_still_reaches_the_account_summary(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.intent_router import resolve_intent

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')

        intent = resolve_intent(
            conversation=conversation,
            message='mi cotizacion ya esta lista?',
            context={'authenticated': True},
        )

        self.assertEqual(intent, 'customer_success')


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


class MessyProductQueryTests(TestCase):
    """The customer types the amount, plurals and words out of order."""

    def setUp(self):
        from config.productos.models import Producto

        self.product = Producto.objects.create(nombre='JARRITO MANGO 24/12.5OZ', activo=True)
        Producto.objects.create(nombre='SODA COCA COLA 6/3LT', activo=True)

    def test_amount_and_packaging_are_dropped_before_searching(self):
        from config.ai_assistant.services.catalog_resolver import strip_quantity_noise

        self.assertEqual(strip_quantity_noise('10 cajas jarritos mango'), 'jarritos mango')
        self.assertEqual(strip_quantity_noise('10 jarritos mango'), 'jarritos mango')
        self.assertEqual(strip_quantity_noise('5 unidades de coca cola'), 'de coca cola')

    def test_a_size_is_never_mistaken_for_an_amount(self):
        from config.ai_assistant.services.catalog_resolver import strip_quantity_noise

        self.assertEqual(strip_quantity_noise('3 litros coca cola'), '3 litros coca cola')

    def test_plural_and_amount_still_find_the_catalog_product(self):
        from config.ai_assistant.services.catalog_resolver import find_products

        result = find_products('10 cajas jarritos mango')

        self.assertEqual(result['query'], 'jarritos mango')
        self.assertEqual(result['products'][0]['name'], 'JARRITO MANGO 24/12.5OZ')

    def test_words_out_of_order_still_find_the_product(self):
        from config.ai_assistant.services.catalog_resolver import find_products

        result = find_products('mango jarrito')

        self.assertEqual(result['products'][0]['name'], 'JARRITO MANGO 24/12.5OZ')

    def test_naming_another_product_starts_a_new_search(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.conversation_state import update_state
        from config.ai_assistant.services.intent_router import resolve_intent

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')
        update_state(conversation, selected_product={'name': 'SODA COCA COLA 6/3LT', 'brand': ''})

        self.assertEqual(
            resolve_intent(
                conversation=conversation,
                message='necesito 10 cajas de jarritos mango',
                context={'authenticated': False},
            ),
            'product_search',
        )

    def test_a_bare_amount_still_belongs_to_the_selected_product(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.conversation_state import update_state
        from config.ai_assistant.services.intent_router import resolve_intent

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')
        update_state(conversation, selected_product={'name': 'SODA COCA COLA 6/3LT', 'brand': ''})

        self.assertEqual(
            resolve_intent(
                conversation=conversation,
                message='quiero 10 cajas',
                context={'authenticated': False},
            ),
            'product_reference',
        )


class PurchaseCallToActionTests(TestCase):
    """A visitor is invited to sign in; a customer is helped into the cart."""

    def setUp(self):
        from config.productos.models import Producto

        Producto.objects.create(nombre='JARRITO MANGO 24/12.5OZ', activo=True)
        self.factory = RequestFactory()

    def _result(self, *, authenticated):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.orchestrator import _purchase_intent_result

        from django.contrib.sessions.backends.db import SessionStore

        request = self.factory.post('/')
        request.user = AnonymousUser()
        request.session = SessionStore()
        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')
        return _purchase_intent_result(
            request,
            conversation,
            {'authenticated': authenticated, 'page': 'home'},
            'necesito 10 cajas de jarritos mango',
            '',
        )

    def test_visitor_is_asked_to_sign_in_to_build_the_quote(self):
        result = self._result(authenticated=False)

        labels = [action['label'] for action in result['suggested_actions']]
        self.assertIn('JARRITO MANGO 24/12.5OZ', result['message'])
        self.assertIn('inicies sesión', result['message'])
        self.assertIn('Iniciar sesión para cotizar', labels)
        self.assertNotIn('Agregar al carrito', labels)

    def test_signed_in_customer_is_offered_the_cart(self):
        result = self._result(authenticated=True)

        labels = [action['label'] for action in result['suggested_actions']]
        self.assertIn('Agregar al carrito', labels)
        self.assertNotIn('Iniciar sesión para cotizar', labels)


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


@override_settings(
    DEMO_MODE=True,
    AI_ASSISTANT_ENABLED=True,
    AI_ASSISTANT_PROVIDER='mock',
    DEMO_BRAND_NAME='Zyntra',
)
class DemoZyntraAssistantTests(TestCase):
    def setUp(self):
        self.config = AssistantConfiguration.get_solo()
        self.config.enabled = False
        self.config.assistant_name = 'Isabella'
        self.config.save(update_fields=['enabled', 'assistant_name'])

    def test_context_exposes_zyntra_guide_without_ltg(self):
        response = self.client.get(reverse('ai_assistant_context'), {'language': 'en', 'page': 'home'})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['enabled'])
        self.assertTrue(payload['demo_assistant'])
        self.assertEqual(payload['assistant_name'], 'Zyntra Guide')
        self.assertIn('Zyntra', payload['welcome_message'])
        self.assertNotIn('Isabella', payload['welcome_message'])
        self.assertNotIn('La Tortilla', payload['welcome_message'])

    def test_message_uses_mock_not_openai(self):
        created = self.client.post(
            reverse('ai_assistant_create_conversation'),
            data=json.dumps({'page': 'home', 'language': 'en'}),
            content_type='application/json',
        )
        conversation_id = created.json()['conversation_id']
        with patch('config.ai_assistant.services.orchestrator.OpenAIClient') as client_cls:
            reply = self.client.post(
                reverse('ai_assistant_conversation_message', args=[conversation_id]),
                data=json.dumps({'message': 'tell me about QuickBooks'}),
                content_type='application/json',
            )
        self.assertEqual(reply.status_code, 200)
        client_cls.assert_not_called()
        body = reply.json()
        self.assertIn('QuickBooks', body['message'])
        self.assertIn('mock', body['message'].lower())
        last = AssistantMessage.objects.filter(
            conversation__public_id=conversation_id,
            role=AssistantMessage.ROLE_ASSISTANT,
        ).latest('created_at')
        self.assertEqual(last.model, 'zyntra-demo-mock')


class LanguageDetectionTests(TestCase):
    def test_spanish_message_is_detected_as_es(self):
        from config.ai_assistant.services.language import detect_message_language

        self.assertEqual(
            detect_message_language('Cómo puedo saber precios de tus productos'),
            'es',
        )

    def test_english_message_is_detected_as_en(self):
        from config.ai_assistant.services.language import detect_message_language

        self.assertEqual(
            detect_message_language('How can I see your product prices?'),
            'en',
        )

    def test_spanish_message_switches_conversation_from_english(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.language import sync_conversation_language_from_message

        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='en')
        language = sync_conversation_language_from_message(
            conversation,
            'Pero quiero obtener la información por este medio',
        )

        self.assertEqual(language, 'es')
        conversation.refresh_from_db()
        self.assertEqual(conversation.language, 'es')


class PriceAccessIntentTests(TestCase):
    def _conversation(self, language='es'):
        from config.ai_assistant.models import AssistantConversation

        return AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language=language)

    def test_how_to_see_prices_is_not_a_catalog_search(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        intent = resolve_intent(
            conversation=self._conversation(),
            message='Cómo puedo saber precios de tus productos',
            context={'authenticated': False},
        )

        self.assertEqual(intent, 'price_access')

    def test_platform_howto_without_product_is_price_access(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        intent = resolve_intent(
            conversation=self._conversation(),
            message='Pero quiero obtener la información por este medio',
            context={'authenticated': False},
        )

        self.assertEqual(intent, 'price_access')

    def test_named_product_price_question_still_searches_catalog(self):
        from config.ai_assistant.services.intent_router import resolve_intent

        intent = resolve_intent(
            conversation=self._conversation(),
            message='quiero el precio de jarritos mango',
            context={'authenticated': False},
        )

        self.assertEqual(intent, 'product_search')

    def test_purchase_handler_skips_conversational_fragments(self):
        from config.ai_assistant.models import AssistantConversation
        from config.ai_assistant.services.orchestrator import _purchase_intent_result

        request = RequestFactory().post('/')
        request.user = AnonymousUser()
        conversation = AssistantConversation.objects.create(visitor_id=uuid.uuid4(), language='es')

        result = _purchase_intent_result(
            request,
            conversation,
            {'authenticated': False, 'page': 'home'},
            'Cómo puedo saber precios de tus productos',
            '',
        )

        self.assertIsNone(result)

    def test_price_access_reply_is_warm_and_offers_whatsapp(self):
        from config.ai_assistant.models import AssistantConfiguration
        from config.ai_assistant.services.orchestrator import _price_access_result

        config = AssistantConfiguration.get_solo()
        config.support_whatsapp = '14045550100'
        config.save(update_fields=['support_whatsapp'])

        result = _price_access_result({'authenticated': False}, language='es')

        self.assertIn('WhatsApp', result['message'])
        self.assertIn('cuenta aprobada', result['message'].lower())
        self.assertEqual(
            result['suggested_actions'][0]['label'],
            'Iniciar sesión',
        )
        self.assertTrue(
            any(
                action['label'] == 'Hablar con el gerente de ventas por WhatsApp'
                for action in result['suggested_actions']
            )
        )

    def test_price_access_reply_follows_english(self):
        from config.ai_assistant.models import AssistantConfiguration
        from config.ai_assistant.services.orchestrator import _price_access_result

        config = AssistantConfiguration.get_solo()
        config.support_whatsapp = '14045550100'
        config.save(update_fields=['support_whatsapp'])

        result = _price_access_result({'authenticated': False}, language='en')

        self.assertIn('approved account', result['message'].lower())
        self.assertTrue(
            any(
                action['label'] == 'Talk with sales manager on WhatsApp'
                for action in result['suggested_actions']
            )
        )
