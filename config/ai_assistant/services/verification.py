import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from config.ai_assistant.models import AssistantVerificationChallenge
from config.clientes.models import Cliente

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
CHALLENGE_TTL_MINUTES = 10
MAX_CHALLENGES_PER_HOUR = 3


def _email_hash(email):
    return hashlib.sha256(str(email or '').strip().lower().encode('utf-8')).hexdigest()


def _code_hash(challenge_id, code):
    secret = settings.SECRET_KEY.encode('utf-8')
    return hashlib.sha256(secret + str(challenge_id).encode('utf-8') + str(code).encode('utf-8')).hexdigest()


def issue_account_status_challenge(email):
    """Issue a non-enumerating email OTP. The return text is identical for unknown emails."""
    normalized_email = str(email or '').strip().lower()
    rate_key = f'ai-assistant:status-otp:{_email_hash(normalized_email)}'
    issued = int(cache.get(rate_key, 0))
    if issued >= MAX_CHALLENGES_PER_HOUR:
        raise VerificationRateLimited()
    cache.set(rate_key, issued + 1, timeout=3600)
    # Pending applications can have a different role while backoffice reviews
    # them. The Cliente record is the authoritative ownership boundary.
    cliente = (
        Cliente.objects.select_related('usuario')
        .annotate(_assistant_email=Lower(Trim('usuario__email')))
        .filter(_assistant_email=normalized_email)
        .first()
    )
    user = cliente.usuario if cliente else None
    code = f'{secrets.randbelow(1_000_000):06d}'
    challenge = AssistantVerificationChallenge.objects.create(
        purpose=AssistantVerificationChallenge.PURPOSE_ACCOUNT_STATUS,
        email_hash=_email_hash(normalized_email),
        user=user,
        cliente=cliente,
        expires_at=timezone.now() + timedelta(minutes=CHALLENGE_TTL_MINUTES),
        code_hash='',
    )
    challenge.code_hash = _code_hash(challenge.public_id, code)
    challenge.save(update_fields=['code_hash'])
    logger.info('AI assistant OTP challenge issued: purpose=%s known_subject=%s', challenge.purpose, bool(user))
    if user:
        try:
            delivered = send_mail(
                'La Tortilla Grocery: código de verificación',
                f'Tu código para consultar el estado de tu solicitud es: {code}. Expira en {CHALLENGE_TTL_MINUTES} minutos.',
                settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL,
                [user.email],
                fail_silently=False,
            )
            logger.info('AI assistant OTP delivery accepted: purpose=%s accepted=%s', challenge.purpose, bool(delivered))
        except Exception:
            # Never expose the email address or provider error to the visitor.
            logger.exception('AI assistant OTP delivery failed: purpose=%s', challenge.purpose)
    return challenge


class VerificationRateLimited(Exception):
    pass


def verify_account_status_challenge(challenge_id, code):
    challenge = AssistantVerificationChallenge.objects.select_related('cliente', 'user').filter(
        public_id=challenge_id,
        purpose=AssistantVerificationChallenge.PURPOSE_ACCOUNT_STATUS,
    ).first()
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= timezone.now()
        or challenge.attempts >= MAX_ATTEMPTS
    ):
        return None
    challenge.attempts += 1
    if not secrets.compare_digest(challenge.code_hash, _code_hash(challenge.public_id, code)):
        challenge.save(update_fields=['attempts'])
        return None
    challenge.verified_at = timezone.now()
    challenge.consumed_at = timezone.now()
    challenge.save(update_fields=['attempts', 'verified_at', 'consumed_at'])
    logger.info('AI assistant OTP verified: purpose=%s', challenge.purpose)
    return challenge.cliente
