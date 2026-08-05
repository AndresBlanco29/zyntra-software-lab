import logging
from urllib.parse import quote

import requests
from django.conf import settings

from .constants import QUICKBOOKS_API_BASE_URLS
from .services import QuickBooksServiceError, ensure_valid_access_token, get_connection


logger = logging.getLogger(__name__)


class QuickBooksAPIError(Exception):
    pass


class QuickBooksAPIClient:
    def __init__(self, *, connection=None):
        self.connection = ensure_valid_access_token(connection=connection or get_connection())

    @property
    def base_url(self):
        return QUICKBOOKS_API_BASE_URLS.get(settings.QUICKBOOKS_ENVIRONMENT, QUICKBOOKS_API_BASE_URLS['sandbox'])

    @property
    def realm_id(self):
        realm_id = self.connection.realm_id
        if not realm_id:
            raise QuickBooksAPIError('QuickBooks realm ID is not configured.')
        return realm_id

    def realm_path(self, suffix):
        return f'/v3/company/{self.realm_id}/{suffix.lstrip("/")}'

    def request(self, method, path, *, params=None, json=None, _retry_auth=True):
        url = f'{self.base_url}{path}'
        response = requests.request(
            method,
            url,
            params=params,
            json=json,
            headers={
                'Authorization': f'Bearer {self.connection.access_token}',
                'Accept': 'application/json',
                'Content-Type': 'application/json',
            },
            timeout=30,
        )
        if response.status_code == 401 and _retry_auth:
            try:
                self.connection = ensure_valid_access_token(connection=self.connection, force_refresh=True)
            except QuickBooksServiceError as exc:
                raise QuickBooksAPIError(f'QuickBooks API authentication failed: {exc}') from exc
            return self.request(method, path, params=params, json=json, _retry_auth=False)
        if not response.ok:
            logger.warning('QuickBooks API request failed: %s %s -> %s', method, url, response.status_code)
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            if isinstance(detail, str):
                lowered = detail.lower()
                if lowered.startswith('<!doctype') or '<html' in lowered:
                    detail = f'HTTP {response.status_code} Bad Request'
                elif len(detail) > 500:
                    detail = detail[:500] + '...'
            raise QuickBooksAPIError(f'QuickBooks API request failed: {detail}')
        return response.json()

    def query(self, statement):
        payload = self.request(
            'GET',
            self.realm_path('query'),
            params={
                'query': statement,
                'minorversion': settings.QUICKBOOKS_API_MINOR_VERSION,
            },
        )
        return payload.get('QueryResponse', payload)

    def _escape_query_value(self, value):
        return str(value or '').replace("'", "\\'")

    def _build_select_statement(self, entity_name, *, where_clause=None, order_by=None, start_position=1, max_results=100):
        statement = f'select * from {entity_name}'
        if where_clause:
            statement = f'{statement} where {where_clause}'
        if order_by:
            statement = f'{statement} orderby {order_by}'
        return f'{statement} startposition {int(start_position)} maxresults {int(max_results)}'

    def find_all(self, entity_name, *, max_results=100, where_clause=None, order_by=None, page_size=100):
        if max_results is not None and int(max_results) <= 0:
            max_results = None
        page_size = max(int(page_size or 100), 1)
        start_position = 1
        remaining = None if max_results is None else int(max_results)
        entities = []

        while True:
            batch_size = page_size if remaining is None else min(page_size, remaining)
            response = self.query(
                self._build_select_statement(
                    entity_name,
                    where_clause=where_clause,
                    order_by=order_by,
                    start_position=start_position,
                    max_results=batch_size,
                )
            )
            batch = response.get(entity_name, [])
            if not batch:
                break
            entities.extend(batch)
            if remaining is not None:
                remaining -= len(batch)
                if remaining <= 0:
                    break
            if len(batch) < batch_size:
                break
            start_position += len(batch)

        return entities

    def find_updated_since(self, entity_name, updated_after, *, max_results=None, page_size=100):
        where_clause = f"MetaData.LastUpdatedTime > '{self._escape_query_value(updated_after)}'"
        return self.find_all(
            entity_name,
            max_results=max_results,
            where_clause=where_clause,
            order_by='MetaData.LastUpdatedTime',
            page_size=page_size,
        )

    def _normalize_query_entities(self, response, entity_name):
        entities = response.get(entity_name, [])
        if isinstance(entities, dict):
            return [entities]
        if isinstance(entities, list):
            return entities
        return []

    def find_attachments_for_entity(self, entity_type, entity_id, *, max_results=10):
        entity_id = str(entity_id or '').strip()
        if not entity_id:
            return []

        type_variants = []
        for candidate in (entity_type, str(entity_type or '').lower(), str(entity_type or '').title()):
            normalized = str(candidate or '').strip()
            if normalized and normalized not in type_variants:
                type_variants.append(normalized)

        attachments = []
        seen_ids = set()
        for type_value in type_variants:
            response = self.query(
                self._build_select_statement(
                    'Attachable',
                    where_clause=(
                        f"AttachableRef.EntityRef.Type = '{self._escape_query_value(type_value)}' "
                        f"and AttachableRef.EntityRef.value = '{self._escape_query_value(entity_id)}'"
                    ),
                    max_results=max_results,
                )
            )
            for attachment in self._normalize_query_entities(response, 'Attachable'):
                attachable_id = str(attachment.get('Id') or '').strip()
                if attachable_id:
                    if attachable_id in seen_ids:
                        continue
                    seen_ids.add(attachable_id)
                attachments.append(attachment)
            if attachments:
                return attachments[:max_results]

        response = self.query(
            self._build_select_statement(
                'Attachable',
                where_clause=f"AttachableRef.EntityRef.value = '{self._escape_query_value(entity_id)}'",
                max_results=max_results,
            )
        )
        for attachment in self._normalize_query_entities(response, 'Attachable'):
            attachable_id = str(attachment.get('Id') or '').strip()
            if attachable_id:
                if attachable_id in seen_ids:
                    continue
                seen_ids.add(attachable_id)
            attachments.append(attachment)
        return attachments[:max_results]

    @staticmethod
    def _looks_like_image_bytes(content):
        data = content or b''
        if len(data) < 8:
            return False
        if data[:3] == b'\xff\xd8\xff':
            return True
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return True
        if data[:6] in (b'GIF87a', b'GIF89a'):
            return True
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return True
        return False

    @staticmethod
    def _is_presigned_document_url(download_url):
        normalized = str(download_url or '').strip().lower()
        return 'financialdocument.platform.intuit.com' in normalized or 'amazonaws.com' in normalized

    def resolve_attachable_temp_download_uri(self, attachable_id, *, _retry_auth=True):
        """
        GET /v3/company/{realm}/download/{attachableId} returns a short-lived
        plain-text TempDownloadUri (not the file bytes).
        """
        attachable_id = str(attachable_id or '').strip()
        if not attachable_id:
            raise QuickBooksAPIError('QuickBooks attachable id is required to resolve a download URI.')

        response = requests.request(
            'GET',
            f'{self.base_url}{self.realm_path(f"download/{attachable_id}")}',
            headers={
                'Authorization': f'Bearer {self.connection.access_token}',
                'Accept': 'text/plain, application/json, */*',
            },
            params={'minorversion': settings.QUICKBOOKS_API_MINOR_VERSION},
            timeout=self._image_download_timeout(),
        )
        if response.status_code == 401 and _retry_auth:
            try:
                self.connection = ensure_valid_access_token(connection=self.connection, force_refresh=True)
            except QuickBooksServiceError as exc:
                raise QuickBooksAPIError(f'QuickBooks API authentication failed: {exc}') from exc
            return self.resolve_attachable_temp_download_uri(attachable_id, _retry_auth=False)
        if not response.ok:
            logger.warning(
                'QuickBooks attachable download URI lookup failed: %s -> %s',
                attachable_id,
                response.status_code,
            )
            raise QuickBooksAPIError(
                f'QuickBooks attachable download URI lookup failed with status {response.status_code}.'
            )

        content_type = str(response.headers.get('Content-Type') or '').lower()
        if content_type.startswith('image/') or self._looks_like_image_bytes(response.content):
            # Some environments stream the file directly from this endpoint.
            return {'bytes': response.content, 'content_type': response.headers.get('Content-Type', content_type)}

        text = (response.text or '').strip().strip('"').strip("'")
        if text.startswith('http://') or text.startswith('https://'):
            return {'url': text}

        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            for key in ('TempDownloadUri', 'TempDownloadUrl', 'tempDownloadUri', 'tempDownloadUrl'):
                candidate = str(payload.get(key) or '').strip()
                if candidate.startswith('http://') or candidate.startswith('https://'):
                    return {'url': candidate}
            nested = payload.get('Attachable') if isinstance(payload.get('Attachable'), dict) else None
            if nested:
                for key in ('TempDownloadUri', 'TempDownloadUrl'):
                    candidate = str(nested.get(key) or '').strip()
                    if candidate.startswith('http://') or candidate.startswith('https://'):
                        return {'url': candidate}

        raise QuickBooksAPIError('QuickBooks download endpoint did not return a TempDownloadUri.')

    def download_attachable_content(self, attachment):
        """
        Download attachable bytes.

        Important: TempDownloadUri hosts are pre-signed. Sending a Bearer token
        there returns 403. The reliable flow is:
        1) OAuth GET /download/{id} → fresh TempDownloadUri (text)
        2) Unauthenticated GET of that URI → file bytes
        """
        attachable_id = str(attachment.get('Id') or '').strip()
        errors = []

        if attachable_id:
            try:
                resolved = self.resolve_attachable_temp_download_uri(attachable_id)
                if resolved.get('bytes') is not None:
                    return resolved['bytes'], resolved.get('content_type') or ''
                return self.download_public_file(resolved['url'])
            except QuickBooksAPIError as exc:
                errors.append(str(exc))

        # Stale query-time URIs often 401; still try without Bearer as a fallback.
        for key in ('TempDownloadUri', 'ThumbnailTempDownloadUri'):
            download_url = str((attachment or {}).get(key) or '').strip()
            if not download_url:
                continue
            try:
                return self.download_public_file(download_url)
            except QuickBooksAPIError as exc:
                errors.append(str(exc))

        file_access_uri = str((attachment or {}).get('FileAccessUri') or '').strip()
        marker = '/download/'
        if file_access_uri and marker in file_access_uri:
            extracted_id = file_access_uri.split(marker, 1)[1].split('?', 1)[0].strip('/')
            if extracted_id and extracted_id != attachable_id:
                try:
                    resolved = self.resolve_attachable_temp_download_uri(extracted_id)
                    if resolved.get('bytes') is not None:
                        return resolved['bytes'], resolved.get('content_type') or ''
                    return self.download_public_file(resolved['url'])
                except QuickBooksAPIError as exc:
                    errors.append(str(exc))

        detail = '; '.join(errors) if errors else 'no downloadable URI'
        raise QuickBooksAPIError(f'QuickBooks attachment download failed: {detail}')

    def _image_download_timeout(self):
        return max(int(getattr(settings, 'QUICKBOOKS_IMAGE_DOWNLOAD_TIMEOUT', 20) or 20), 5)

    def download_authenticated_file(self, download_url, *, timeout=None, _retry_auth=True):
        # Pre-signed Intuit document URLs reject Bearer auth with 403.
        if self._is_presigned_document_url(download_url):
            return self.download_public_file(download_url, timeout=timeout)

        response = requests.request(
            'GET',
            download_url,
            headers={
                'Authorization': f'Bearer {self.connection.access_token}',
                'Accept': '*/*',
            },
            timeout=timeout or self._image_download_timeout(),
        )
        if response.status_code == 401 and _retry_auth:
            try:
                self.connection = ensure_valid_access_token(connection=self.connection, force_refresh=True)
            except QuickBooksServiceError as exc:
                raise QuickBooksAPIError(f'QuickBooks API authentication failed: {exc}') from exc
            return self.download_authenticated_file(download_url, timeout=timeout, _retry_auth=False)
        if not response.ok:
            logger.warning('QuickBooks authenticated download failed: %s -> %s', download_url, response.status_code)
            raise QuickBooksAPIError(f'QuickBooks authenticated download failed with status {response.status_code}.')
        return response.content, response.headers.get('Content-Type', '')

    def download_public_file(self, download_url, *, timeout=None):
        response = requests.request(
            'GET',
            download_url,
            headers={'Accept': '*/*'},
            timeout=timeout or self._image_download_timeout(),
            allow_redirects=True,
        )
        if not response.ok:
            logger.warning('QuickBooks file download failed: %s -> %s', download_url, response.status_code)
            raise QuickBooksAPIError(f'QuickBooks file download failed with status {response.status_code}.')
        return response.content, response.headers.get('Content-Type', '')

    def find_by_id(self, entity_name, entity_id):
        response = self.query(f"select * from {entity_name} where Id = '{entity_id}' maxresults 1")
        entities = response.get(entity_name, [])
        return entities[0] if entities else None

    def read_entity(self, entity_name, entity_id):
        entity_id = str(entity_id or '').strip()
        if not entity_id:
            return None
        response = self.request(
            'GET',
            self.realm_path(f'{entity_name.lower()}/{quote(entity_id, safe="")}'),
            params={'minorversion': settings.QUICKBOOKS_API_MINOR_VERSION},
        )
        return response.get(entity_name) or response.get(entity_name.lower()) or response.get(entity_name.title())

    def find_one_by_name(self, entity_name, display_name):
        escaped_name = self._escape_query_value(display_name)
        response = self.query(f"select * from {entity_name} where Name = '{escaped_name}' maxresults 1")
        entities = response.get(entity_name, [])
        return entities[0] if entities else None

    def find_one_by_display_name(self, entity_name, display_name):
        escaped_name = self._escape_query_value(display_name)
        response = self.query(f"select * from {entity_name} where DisplayName = '{escaped_name}' maxresults 1")
        entities = response.get(entity_name, [])
        return entities[0] if entities else None

    def create_entity(self, entity_name, payload):
        response = self.request(
            'POST',
            self.realm_path(entity_name.lower()),
            params={'minorversion': settings.QUICKBOOKS_API_MINOR_VERSION},
            json=payload,
        )
        return response.get(entity_name, response)

    def update_entity(self, entity_name, payload):
        response = self.request(
            'POST',
            self.realm_path(entity_name.lower()),
            params={'operation': 'update', 'minorversion': settings.QUICKBOOKS_API_MINOR_VERSION},
            json=payload,
        )
        return response.get(entity_name, response)

    def create_customer(self, payload):
        return self.create_entity('Customer', payload)

    def update_customer(self, payload):
        return self.update_entity('Customer', payload)

    def create_item(self, payload):
        return self.create_entity('Item', payload)

    def update_item(self, payload):
        return self.update_entity('Item', payload)

    def create_invoice(self, payload):
        return self.create_entity('Invoice', payload)

    def update_invoice(self, payload):
        return self.update_entity('Invoice', payload)

    def create_payment(self, payload):
        return self.create_entity('Payment', payload)

    def create_credit_memo(self, payload):
        return self.create_entity('CreditMemo', payload)

    def update_credit_memo(self, payload):
        return self.update_entity('CreditMemo', payload)

    def get_company_info(self):
        payload = self.request(
            'GET',
            self.realm_path(f'companyinfo/{self.realm_id}'),
            params={'minorversion': settings.QUICKBOOKS_API_MINOR_VERSION},
        )
        return payload.get('CompanyInfo', payload)