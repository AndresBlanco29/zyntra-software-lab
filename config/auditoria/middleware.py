import logging

from config.auditoria.services import record_audit_event_from_request

logger = logging.getLogger(__name__)


class AuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            record_audit_event_from_request(request, response)
        except Exception:
            logger.exception('Audit middleware failed')
        return response
