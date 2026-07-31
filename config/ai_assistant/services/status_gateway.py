"""Read-only, ownership-scoped status adapters for Assistant Function Calling."""
from django.utils import timezone


class StatusGateway:
    def get_status(self, *, cliente, entity_type, entity_id=None):
        if not cliente:
            return {'error': 'Verified customer identity is required.'}
        adapters = {
            'account': self._account,
            'quote': self._quote,
            'order': self._order,
            'invoice': self._invoice,
            'customer_success': self._customer_success,
        }
        adapter = adapters.get(entity_type)
        return adapter(cliente, entity_id) if adapter else {'error': 'Unsupported status type.'}

    def _account(self, cliente, _entity_id):
        status_labels = {
            'PENDIENTE': 'pending_review',
            'APROBADO': 'approved',
            'RECHAZADO': 'rejected',
        }
        return {
            'entity_type': 'account',
            'status': status_labels.get(cliente.estado_revision, 'pending_review'),
            'submitted_at': cliente.creado_en.isoformat() if cliente.creado_en else None,
            'approved_at': cliente.aprobado_en.isoformat() if cliente.aprobado_en else None,
            'needs_correction': bool(cliente.correction_requested_at),
        }

    def _quote(self, cliente, entity_id):
        from config.cotizaciones.models import Cotizacion
        quote = Cotizacion.objects.filter(cliente=cliente, pk=entity_id).first() if entity_id else None
        if not quote:
            return {'error': 'Quote not found.'}
        return {'entity_type': 'quote', 'id': quote.id, 'status': quote.estado, 'created_at': quote.fecha.isoformat()}

    def _order(self, cliente, entity_id):
        from config.pedidos.models import Pedido
        order = Pedido.objects.filter(cliente=cliente, pk=entity_id).first() if entity_id else None
        if not order:
            return {'error': 'Order not found.'}
        return {'entity_type': 'order', 'id': order.id, 'status': order.estado, 'created_at': order.creada_en.isoformat()}

    def _invoice(self, cliente, entity_id):
        from config.facturacion.models import Invoice

        invoice = Invoice.objects.filter(cliente=cliente, pk=entity_id).first() if entity_id else None
        if not invoice:
            return {'error': 'Invoice not found.'}
        return {
            'entity_type': 'invoice',
            'id': invoice.id,
            'number': invoice.numero,
            'payment_status': invoice.qb_payment_status,
            'due_date': invoice.qb_due_date.isoformat() if invoice.qb_due_date else None,
            'balance': str(invoice.saldo_cliente),
        }

    def _customer_success(self, cliente, _entity_id):
        from config.ai_assistant.services.customer_success import build_customer_success_summary

        return build_customer_success_summary(cliente=cliente)
