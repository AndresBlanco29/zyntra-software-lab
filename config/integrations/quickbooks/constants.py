from django.utils.translation import gettext_lazy as _


QUICKBOOKS_SYNC_STATUS_PENDING = 'PENDING'
QUICKBOOKS_SYNC_STATUS_SYNCED = 'SYNCED'
QUICKBOOKS_SYNC_STATUS_FAILED = 'FAILED'

QUICKBOOKS_SYNC_STATUS_CHOICES = (
    (QUICKBOOKS_SYNC_STATUS_PENDING, _('Pending')),
    (QUICKBOOKS_SYNC_STATUS_SYNCED, _('Synced')),
    (QUICKBOOKS_SYNC_STATUS_FAILED, _('Failed')),
)

QUICKBOOKS_AUTHORIZATION_URL = 'https://appcenter.intuit.com/connect/oauth2'
QUICKBOOKS_TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
QUICKBOOKS_API_BASE_URLS = {
    'sandbox': 'https://sandbox-quickbooks.api.intuit.com',
    'production': 'https://quickbooks.api.intuit.com',
}