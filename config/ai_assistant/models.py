import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class AssistantConfiguration(models.Model):
    """Single BackOffice-managed configuration for the public/customer assistant."""

    assistant_name = models.CharField(max_length=80, default='Paco')
    welcome_message = models.TextField(
        default='Hola.\n\nSoy Paco, el asistente virtual de La Tortilla Grocery.\n\n¿Quieres que te ayude a registrarte?'
    )
    personality = models.TextField(default='Amable, claro, proactivo y orientado a ayudar a completar una compra.')
    sales_goal = models.TextField(default='Guiar al cliente hacia el siguiente paso útil: registro, catálogo, carrito, solicitud o confirmación.')
    system_prompt = models.TextField(blank=True)
    default_language = models.CharField(max_length=8, default='es')
    chat_model = models.CharField(max_length=100, default='gpt-4.1-mini')
    embedding_model = models.CharField(max_length=100, default='text-embedding-3-small')
    temperature = models.DecimalField(max_digits=3, decimal_places=2, default='0.30')
    max_messages_per_hour = models.PositiveIntegerField(default=30)
    max_message_chars = models.PositiveIntegerField(default=2000)
    handoff_url = models.URLField(blank=True)
    support_phone = models.CharField(max_length=40, default='+1 (470) 967-2782')
    support_whatsapp = models.CharField(max_length=40, default='17866516897')
    support_email = models.EmailField(default='latortillagrocery@gmail.com')
    location_address = models.TextField(blank=True)
    location_map_url = models.URLField(blank=True)
    delivery_coverage = models.CharField(max_length=250, default='Georgia, Alabama y Tennessee')
    enabled = models.BooleanField(default=False)
    enable_home = models.BooleanField(default=True)
    enable_catalog = models.BooleanField(default=True)
    enable_customer_portal = models.BooleanField(default=True)
    retention_days = models.PositiveIntegerField(default=90)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'AI Assistant configuration'
        verbose_name_plural = 'AI Assistant configuration'

    @classmethod
    def get_solo(cls):
        config, _ = cls.objects.get_or_create(
            pk=1,
            defaults={
                'enabled': bool(getattr(settings, 'AI_ASSISTANT_ENABLED', False)),
                'chat_model': getattr(settings, 'AI_ASSISTANT_CHAT_MODEL', 'gpt-4.1-mini'),
                'embedding_model': getattr(settings, 'AI_ASSISTANT_EMBEDDING_MODEL', 'text-embedding-3-small'),
                'max_messages_per_hour': int(getattr(settings, 'AI_ASSISTANT_MAX_MESSAGES_PER_HOUR', 30)),
                'max_message_chars': int(getattr(settings, 'AI_ASSISTANT_MAX_MESSAGE_CHARS', 2000)),
            },
        )
        return config

    def __str__(self):
        return f'{self.assistant_name} configuration'


class AssistantKnowledgeDocument(models.Model):
    STATUS_DRAFT = 'DRAFT'
    STATUS_PUBLISHED = 'PUBLISHED'
    STATUS_CHOICES = ((STATUS_DRAFT, 'Draft'), (STATUS_PUBLISHED, 'Published'))

    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    content = models.TextField()
    category = models.CharField(max_length=80, blank=True)
    language = models.CharField(max_length=8, default='es')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)
    version = models.PositiveIntegerField(default=1)
    source_url = models.CharField(max_length=300, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    published_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('title',)

    def __str__(self):
        return self.title


class AssistantKnowledgeChunk(models.Model):
    document = models.ForeignKey(AssistantKnowledgeDocument, on_delete=models.CASCADE, related_name='chunks')
    position = models.PositiveIntegerField()
    content = models.TextField()
    embedding = models.JSONField(default=list, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('document_id', 'position')
        unique_together = ('document', 'position')


class AssistantProductAlias(models.Model):
    """Auditable commercial synonyms used only by the catalog resolver."""

    alias = models.CharField(max_length=160, unique=True)
    product = models.ForeignKey(
        'productos.Producto',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='assistant_aliases',
    )
    brand = models.ForeignKey(
        'productos.Marca',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='assistant_aliases',
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Assistant product alias'
        verbose_name_plural = 'Assistant product aliases'


class AssistantConversation(models.Model):
    STATUS_OPEN = 'OPEN'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CHOICES = ((STATUS_OPEN, 'Open'), (STATUS_CLOSED, 'Closed'))

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    visitor_id = models.UUIDField(db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='assistant_conversations')
    cliente = models.ForeignKey('clientes.Cliente', null=True, blank=True, on_delete=models.SET_NULL, related_name='assistant_conversations')
    language = models.CharField(max_length=8, default='es')
    first_page = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    summary = models.TextField(blank=True)
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-last_activity_at',)


class AssistantMessage(models.Model):
    ROLE_USER = 'user'
    ROLE_ASSISTANT = 'assistant'
    ROLE_TOOL = 'tool'
    ROLE_SYSTEM = 'system'
    ROLE_CHOICES = ((ROLE_USER, 'User'), (ROLE_ASSISTANT, 'Assistant'), (ROLE_TOOL, 'Tool'), (ROLE_SYSTEM, 'System'))

    conversation = models.ForeignKey(AssistantConversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=12, choices=ROLE_CHOICES)
    content = models.TextField()
    redacted_content = models.TextField(blank=True)
    tool_name = models.CharField(max_length=100, blank=True)
    tool_payload = models.JSONField(default=dict, blank=True)
    model = models.CharField(max_length=100, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created_at',)


class AssistantUserState(models.Model):
    visitor_id = models.UUIDField(unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='assistant_state')
    cliente = models.ForeignKey('clientes.Cliente', null=True, blank=True, on_delete=models.SET_NULL, related_name='assistant_state')
    onboarding_completed = models.BooleanField(default=False)
    consented_at = models.DateTimeField(blank=True, null=True)
    preferences = models.JSONField(default=dict, blank=True)
    last_notified_events = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class AssistantCustomerSuccessProfile(models.Model):
    """Cross-session customer success memory; source records remain in domain apps."""

    cliente = models.OneToOneField(
        'clientes.Cliente',
        on_delete=models.CASCADE,
        related_name='assistant_success_profile',
    )
    first_login_at = models.DateTimeField(blank=True, null=True)
    last_login_at = models.DateTimeField(blank=True, null=True)
    last_conversation_at = models.DateTimeField(blank=True, null=True)
    last_module = models.CharField(max_length=80, blank=True)
    last_tour = models.CharField(max_length=80, blank=True)
    onboarding_learned = models.BooleanField(default=False)
    first_order_at = models.DateTimeField(blank=True, null=True)
    last_order_id = models.PositiveIntegerField(blank=True, null=True)
    recently_viewed_products = models.JSONField(default=list, blank=True)
    help_topics = models.JSONField(default=list, blank=True)
    event_marks = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class AssistantVisitorProfile(models.Model):
    """Anonymous first-party visitor state; never used as an authorization credential."""

    visitor_id = models.UUIDField(unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assistant_visitor_profiles',
    )
    cliente = models.ForeignKey(
        'clientes.Cliente',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assistant_visitor_profiles',
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    first_visit_prompted_at = models.DateTimeField(blank=True, null=True)
    quiet_until = models.DateTimeField(blank=True, null=True)
    preferences = models.JSONField(default=dict, blank=True)


class AssistantVerificationChallenge(models.Model):
    PURPOSE_ACCOUNT_STATUS = 'ACCOUNT_STATUS'
    PURPOSE_CHOICES = ((PURPOSE_ACCOUNT_STATUS, 'Account status'),)

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    purpose = models.CharField(max_length=40, choices=PURPOSE_CHOICES)
    email_hash = models.CharField(max_length=64, db_index=True)
    code_hash = models.CharField(max_length=128)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    cliente = models.ForeignKey('clientes.Cliente', null=True, blank=True, on_delete=models.CASCADE)
    expires_at = models.DateTimeField(db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(blank=True, null=True)
    consumed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AssistantGuidedTourProgress(models.Model):
    visitor_id = models.UUIDField()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    tour_key = models.CharField(max_length=80)
    current_step = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)
    dismissed = models.BooleanField(default=False)
    context = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('visitor_id', 'tour_key')


class AssistantDomainEvent(models.Model):
    TYPE_REGISTRATION_SUBMITTED = 'REGISTRATION_SUBMITTED'
    TYPE_ACCOUNT_APPROVED = 'ACCOUNT_APPROVED'
    TYPE_ACCOUNT_NEEDS_CORRECTION = 'ACCOUNT_NEEDS_CORRECTION'
    TYPE_QUOTE_READY = 'QUOTE_READY'
    TYPE_ORDER_DISPATCHED = 'ORDER_DISPATCHED'
    TYPE_ORDER_DELIVERED = 'ORDER_DELIVERED'
    TYPE_CHOICES = (
        (TYPE_REGISTRATION_SUBMITTED, 'Registration submitted'),
        (TYPE_ACCOUNT_APPROVED, 'Account approved'),
        (TYPE_ACCOUNT_NEEDS_CORRECTION, 'Account needs correction'),
        (TYPE_QUOTE_READY, 'Quote ready'),
        (TYPE_ORDER_DISPATCHED, 'Order dispatched'),
        (TYPE_ORDER_DELIVERED, 'Order delivered'),
    )

    cliente = models.ForeignKey('clientes.Cliente', on_delete=models.CASCADE, related_name='assistant_events')
    event_type = models.CharField(max_length=40, choices=TYPE_CHOICES)
    entity_type = models.CharField(max_length=80, blank=True)
    entity_id = models.CharField(max_length=80, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    consumed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)


class AssistantPendingAction(models.Model):
    """One-time server-side action awaiting an explicit customer confirmation."""

    STATUS_PENDING = 'PENDING'
    STATUS_CONFIRMED = 'CONFIRMED'
    STATUS_EXPIRED = 'EXPIRED'
    STATUS_CHOICES = (
        (STATUS_PENDING, 'Pending'),
        (STATUS_CONFIRMED, 'Confirmed'),
        (STATUS_EXPIRED, 'Expired'),
    )

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    visitor_id = models.UUIDField(db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    cliente = models.ForeignKey('clientes.Cliente', null=True, blank=True, on_delete=models.CASCADE)
    action_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at',)
