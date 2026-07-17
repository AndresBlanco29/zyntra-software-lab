from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.messages import get_messages
from django.contrib.auth.models import Group
from .models import Usuario
from .permissions import (
    build_permission_overrides_for_role,
    build_permission_sections,
    get_default_permissions_for_role,
    get_permission_summary_labels,
    get_redirect_url_for_user,
    internal_permission_required,
)
from .us_locations import US_STATE_CITIES
from config.clientes.models import Cliente
from config.clientes.assignment import (
    assign_all_approved_clientes_to_vendedor,
    get_active_vendedores_queryset,
    sync_vendedor_cliente_assignments,
)
from config.core.models import Testimonio, HomeContenido, ensure_homecontenido_quienes_schema
from config.integrations.quickbooks.services import get_connection_status
from config.integrations.quickbooks.views import get_dashboard_sync_context
from config.productos.models import Producto, Marca
from django.contrib.auth.decorators import login_required
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.contrib.auth.hashers import make_password
from django.core.mail import EmailMultiAlternatives
from django.http import JsonResponse, FileResponse, Http404, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.utils.translation import gettext as _
from django.utils import timezone
from django.db import transaction
from django.db import OperationalError, ProgrammingError
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.conf import settings
import mimetypes
import os
import re
import urllib.request
import urllib.error


def _clear_pending_messages(request):
    storage = get_messages(request)
    for _message in storage:
        pass
    storage.used = True
import logging
import json

from .forms import CustomerPasswordResetForm, CustomerSetPasswordForm
from config.productos.models import normalize_price_tier

logger = logging.getLogger(__name__)


def _registration_context(**extra):
    context = {
        'us_locations_json': json.dumps(US_STATE_CITIES),
        'us_states': sorted(US_STATE_CITIES.keys()),
    }
    context.update(extra)
    return context


def _is_valid_registration_document(value):
    return bool(re.fullmatch(r'\d{8,9}', (value or '').strip()))


def _build_client_correction_url(request, cliente):
    return request.build_absolute_uri(reverse('corregir_solicitud_cliente', args=[str(cliente.correction_token)]))


def _attach_filefield_to_email(email, field_file):
    if not field_file:
        return False

    filename = os.path.basename(field_file.name) or 'attachment'
    content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

    try:
        field_file.open('rb')
        email.attach(filename, field_file.read(), content_type)
        return True
    except Exception as exc:
        logger.warning('No se pudo adjuntar archivo %s al correo: %s', filename, exc)
        return False
    finally:
        try:
            field_file.close()
        except Exception:
            pass


def _send_client_decision_email(*, client_email, client_name, approved, company_name, rejection_reason='', correction_url='', rejection_example=None):
    if not client_email:
        return 'no-email'

    resend_api_key = (settings.ANYMAIL.get('RESEND_API_KEY') or '').strip()

    if (
        settings.EMAIL_BACKEND == 'anymail.backends.resend.EmailBackend'
        and (not resend_api_key or resend_api_key == 'tu_api_key_real')
    ):
        raise RuntimeError('RESEND_API_KEY is missing')

    subject = (
        'La Tortilla Grocery - Wholesale account approved'
        if approved
        else 'La Tortilla Grocery - Wholesale account request update'
    )
    preview_text = (
        'Your wholesale account has been approved.'
        if approved
        else 'Your wholesale account request needs corrections before it can be approved.'
    )
    html_content = render_to_string(
        'emails/cliente_aprobacion_estado.html',
        {
            'client_name': client_name,
            'company_name': company_name,
            'approved': approved,
            'preview_text': preview_text,
            'rejection_reason': rejection_reason,
            'correction_url': correction_url,
            'has_rejection_example': bool(rejection_example),
        },
    )
    text_content = (
        f'Hello {client_name},\n\n'
        + (
            'Your wholesale account request for La Tortilla Grocery has been approved. '
            'You can now sign in with the credentials you registered.'
            if approved
            else 'Your wholesale account request for La Tortilla Grocery needs corrections before it can be approved.'
        )
        + (f'\n\nCompany: {company_name}' if company_name else '')
        + (f'\n\nReason: {rejection_reason}' if rejection_reason else '')
        + (f'\n\nCorrection link: {correction_url}' if correction_url else '')
        + '\n\nLa Tortilla Grocery'
    )

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL,
        to=[client_email],
    )
    email.attach_alternative(html_content, 'text/html')

    if not approved:
        _attach_filefield_to_email(email, rejection_example)

    email.send(fail_silently=False)
    return 'sent'


def _get_or_create_home_contenido():
    legacy_fields = [
        'id',
        'hero_titulo_principal',
        'hero_titulo_principal_en',
        'hero_titulo_resaltado',
        'hero_titulo_resaltado_en',
        'hero_titulo_final',
        'hero_titulo_final_en',
        'hero_subtitulo',
        'hero_subtitulo_en',
        'hero_boton_texto',
        'hero_boton_texto_en',
        'cta_titulo',
        'cta_titulo_en',
        'cta_boton_registro_texto',
        'cta_boton_registro_texto_en',
        'cta_boton_catalogo_texto',
        'cta_boton_catalogo_texto_en',
        'activo',
        'actualizado',
    ]

    def with_fallback_defaults(instance):
        if instance is None:
            instance = HomeContenido(activo=True)

        deferred_fields = set()
        if getattr(instance, 'pk', None):
            try:
                deferred_fields = instance.get_deferred_fields()
            except Exception:
                deferred_fields = set()

        for field_name in (
            'quienes_titulo', 'quienes_titulo_en', 'quienes_descripcion', 'quienes_descripcion_en',
            'beneficio_1_titulo', 'beneficio_1_titulo_en', 'beneficio_1_subtitulo', 'beneficio_1_subtitulo_en',
            'beneficio_2_titulo', 'beneficio_2_titulo_en', 'beneficio_2_subtitulo', 'beneficio_2_subtitulo_en',
            'beneficio_3_titulo', 'beneficio_3_titulo_en', 'beneficio_3_subtitulo', 'beneficio_3_subtitulo_en',
            'beneficio_4_titulo', 'beneficio_4_titulo_en', 'beneficio_4_subtitulo', 'beneficio_4_subtitulo_en',
            'estadistica_1_valor', 'estadistica_1_valor_en', 'estadistica_1_label', 'estadistica_1_label_en',
            'estadistica_2_valor', 'estadistica_2_valor_en', 'estadistica_2_label', 'estadistica_2_label_en',
            'estadistica_3_valor', 'estadistica_3_valor_en', 'estadistica_3_label', 'estadistica_3_label_en',
            'footer_empresa_titulo', 'footer_empresa_titulo_en', 'footer_empresa_descripcion', 'footer_empresa_descripcion_en',
            'footer_contacto_titulo', 'footer_contacto_titulo_en', 'footer_contacto_direccion_linea_1',
            'footer_contacto_direccion_linea_2', 'footer_contacto_email', 'footer_contacto_telefono',
        ):
            current_value = instance.__dict__.get(field_name)
            if (
                field_name in deferred_fields
                or field_name not in instance.__dict__
                or current_value == field_name
            ):
                default_value = HomeContenido._meta.get_field(field_name).get_default()
                setattr(instance, field_name, default_value)

        return instance

    try:
        contenido = HomeContenido.objects.order_by('-actualizado').first()
        if contenido is None:
            contenido = HomeContenido.objects.create(activo=True)
        return with_fallback_defaults(contenido)
    except (OperationalError, ProgrammingError):
        try:
            ensure_homecontenido_quienes_schema()
            contenido = HomeContenido.objects.order_by('-actualizado').first()
            if contenido is None:
                contenido = HomeContenido.objects.create(activo=True)
            return with_fallback_defaults(contenido)
        except Exception:
            pass

        contenido = HomeContenido.objects.only(*legacy_fields).order_by('-actualizado').first()
        return with_fallback_defaults(contenido)


def _is_admin_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.role == 'admin'))


def _is_backoffice_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.role in {'admin', 'backoffice'}))


def _get_allowed_internal_roles():
    return {'vendedor', 'backoffice', 'seleccionador', 'driver'}


def _get_internal_role_label(role):
    return {
        'vendedor': _('Sales'),
        'backoffice': _('BackOffice'),
        'seleccionador': _('Selector'),
        'driver': _('Driver'),
    }.get(role, _('Internal user'))


def _resolve_internal_role(value, *, fallback='vendedor'):
    role = (value or fallback or '').strip().lower()
    return role if role in _get_allowed_internal_roles() else fallback


def _internal_users_queryset():
    return Usuario.objects.filter(role__in=sorted(_get_allowed_internal_roles())).order_by('first_name', 'last_name', 'username')


def _redirect_for_user(user):
    return get_redirect_url_for_user(user)


def _resolve_login_redirect(user, next_url=None):
    next_url = (next_url or '').strip()
    if next_url.startswith('/') and not next_url.startswith('//'):
        return next_url
    return _redirect_for_user(user)


def _build_internal_permission_context(role, overrides=None):
    return {
        'permission_sections': build_permission_sections(role=role, overrides=overrides or {}),
        'all_permission_codes': [permission['code'] for permission in build_permission_sections(role=role, overrides=overrides or {}) for permission in permission['permissions']],
    }


def _get_internal_role_permission_defaults():
    return {
        role: sorted(get_default_permissions_for_role(role))
        for role in sorted(_get_allowed_internal_roles())
    }


def _cloudinary_download_url_for_file(field_file):
    """Resuelve una URL usable de Cloudinary verificando el recurso real."""
    try:
        from cloudinary import api
        from cloudinary.models import CLOUDINARY_FIELD_DB_RE
        from cloudinary.utils import private_download_url
    except Exception:
        return None, None

    file_name = (field_file.name or '').lstrip('/')
    if not file_name:
        return None, None

    public_id_candidates = []

    def add_public_id(candidate):
        candidate = (candidate or '').strip().lstrip('/')
        if candidate and candidate not in public_id_candidates:
            public_id_candidates.append(candidate)

    add_public_id(file_name)
    if file_name.startswith('media/'):
        add_public_id(file_name[6:])

    match = re.match(CLOUDINARY_FIELD_DB_RE, file_name)
    parsed_format = None
    if match:
        add_public_id(match.group('public_id'))
        parsed_format = match.group('format')

    expanded_ids = []
    for public_id_candidate in public_id_candidates:
        if public_id_candidate not in expanded_ids:
            expanded_ids.append(public_id_candidate)
        base_public_id, _ = os.path.splitext(public_id_candidate)
        if base_public_id and base_public_id not in expanded_ids:
            expanded_ids.append(base_public_id)

    extension = os.path.splitext(file_name)[1].lstrip('.').lower() or parsed_format or None

    candidates = [
        *[
            {'public_id': public_id_candidate, 'resource_type': 'image', 'type': 'upload'}
            for public_id_candidate in expanded_ids
        ],
        *[
            {'public_id': public_id_candidate, 'resource_type': 'image', 'type': 'authenticated'}
            for public_id_candidate in expanded_ids
        ],
        *[
            {'public_id': public_id_candidate, 'resource_type': 'image', 'type': 'private'}
            for public_id_candidate in expanded_ids
        ],
        *[
            {'public_id': public_id_candidate, 'resource_type': 'raw', 'type': 'upload'}
            for public_id_candidate in expanded_ids
        ],
        *[
            {'public_id': public_id_candidate, 'resource_type': 'raw', 'type': 'authenticated'}
            for public_id_candidate in expanded_ids
        ],
        *[
            {'public_id': public_id_candidate, 'resource_type': 'raw', 'type': 'private'}
            for public_id_candidate in expanded_ids
        ],
    ]

    seen = set()
    for option in candidates:
        key = (option['public_id'], option['resource_type'], option['type'])
        if key in seen or not option['public_id']:
            continue
        seen.add(key)

        try:
            resource = api.resource(
                option['public_id'],
                resource_type=option['resource_type'],
                type=option['type'],
            )
        except Exception:
            continue

        resource_public_id = resource.get('public_id') or option['public_id']
        resource_type = resource.get('resource_type') or option['resource_type']
        delivery_type = resource.get('type') or option['type']
        resource_format = resource.get('format') or extension

        resolved_name = os.path.basename(resource_public_id)
        if resource_format and not os.path.splitext(resolved_name)[1]:
            resolved_name = f"{resolved_name}.{resource_format}"

        if delivery_type in {'authenticated', 'private'}:
            try:
                download_url = private_download_url(
                    resource_public_id,
                    resource_format,
                    resource_type=resource_type,
                    type=delivery_type,
                    secure=True,
                )
                if download_url:
                    return download_url, resolved_name
            except Exception:
                continue

        secure_url = resource.get('secure_url') or resource.get('url')
        if secure_url:
            return secure_url, resolved_name

    return None, None


def _probe_cloudinary_certificate(field_file):
    """Prueba variantes de URLs de Cloudinary y devuelve el primer archivo accesible."""
    try:
        from cloudinary.models import CLOUDINARY_FIELD_DB_RE
        from cloudinary.utils import cloudinary_url, private_download_url
    except Exception:
        return None

    file_name = (field_file.name or '').strip().lstrip('/')
    if not file_name:
        return None

    public_ids = []

    def add_candidate(value):
        value = (value or '').strip().lstrip('/')
        if value and value not in public_ids:
            public_ids.append(value)

    add_candidate(file_name)
    if file_name.startswith('media/'):
        add_candidate(file_name[6:])

    match = re.match(CLOUDINARY_FIELD_DB_RE, file_name)
    if match:
        add_candidate(match.group('public_id'))

    expanded_ids = []
    for candidate in public_ids:
        if candidate not in expanded_ids:
            expanded_ids.append(candidate)
        base_candidate, _ = os.path.splitext(candidate)
        if base_candidate and base_candidate not in expanded_ids:
            expanded_ids.append(base_candidate)

    extension = os.path.splitext(file_name)[1].lstrip('.').lower() or None
    remote_urls = []

    def add_url(url, resolved_name):
        if not url:
            return
        remote_urls.append((url, resolved_name))

    for public_id in expanded_ids:
        base_name = os.path.basename(public_id)
        for resource_type in ('image', 'raw'):
            for delivery_type in ('upload', 'authenticated', 'private'):
                format_value = extension if resource_type == 'image' else None
                try:
                    generated_url, _ = cloudinary_url(
                        public_id,
                        resource_type=resource_type,
                        type=delivery_type,
                        format=format_value,
                        sign_url=delivery_type != 'upload',
                        secure=True,
                    )
                    add_url(generated_url, base_name)
                except Exception:
                    pass

                if extension:
                    try:
                        private_url = private_download_url(
                            public_id,
                            extension,
                            resource_type=resource_type,
                            type=delivery_type,
                            secure=True,
                        )
                        add_url(private_url, f"{base_name}.{extension}" if not os.path.splitext(base_name)[1] else base_name)
                    except Exception:
                        pass

    seen_urls = set()
    for remote_url, resolved_name in remote_urls:
        if remote_url in seen_urls:
            continue
        seen_urls.add(remote_url)

        try:
            with urllib.request.urlopen(remote_url, timeout=20) as remote_file:
                file_bytes = remote_file.read()
                remote_content_type = remote_file.headers.get_content_type()
            final_name = _ensure_extension(resolved_name or os.path.basename(file_name), remote_content_type)
            return file_bytes, remote_content_type, final_name
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403, 404):
                continue
        except Exception:
            continue

    return None


def _cloudinary_pdf_preview(field_file):
    """Si Cloudinary bloquea el PDF original, entrega la primera pagina como imagen."""
    try:
        from cloudinary import api
        from cloudinary.models import CLOUDINARY_FIELD_DB_RE
        from cloudinary.utils import cloudinary_url
    except Exception:
        return None

    file_name = (field_file.name or '').strip().lstrip('/')
    if not file_name:
        return None

    public_ids = []

    def add_candidate(value):
        value = (value or '').strip().lstrip('/')
        if value and value not in public_ids:
            public_ids.append(value)

    add_candidate(file_name)
    if file_name.startswith('media/'):
        add_candidate(file_name[6:])

    match = re.match(CLOUDINARY_FIELD_DB_RE, file_name)
    if match:
        add_candidate(match.group('public_id'))

    expanded_ids = []
    for candidate in public_ids:
        if candidate not in expanded_ids:
            expanded_ids.append(candidate)
        base_candidate, _ = os.path.splitext(candidate)
        if base_candidate and base_candidate not in expanded_ids:
            expanded_ids.append(base_candidate)

    for public_id in expanded_ids:
        for delivery_type in ('upload', 'authenticated', 'private'):
            try:
                resource = api.resource(public_id, resource_type='image', type=delivery_type)
            except Exception:
                continue

            if (resource.get('format') or '').lower() != 'pdf':
                continue

            try:
                preview_url, _ = cloudinary_url(
                    resource.get('public_id') or public_id,
                    resource_type='image',
                    type=resource.get('type') or delivery_type,
                    format='jpg',
                    page=1,
                    version=resource.get('version'),
                    secure=True,
                    sign_url=(resource.get('type') or delivery_type) != 'upload',
                )

                with urllib.request.urlopen(preview_url, timeout=20) as remote_file:
                    file_bytes = remote_file.read()
                    remote_content_type = remote_file.headers.get_content_type()

                preview_name = os.path.basename(resource.get('public_id') or public_id)
                preview_name = _ensure_extension(preview_name, remote_content_type or 'image/jpeg')
                return file_bytes, remote_content_type or 'image/jpeg', preview_name
            except Exception:
                continue

    return None


def _ensure_extension(file_name, content_type):
    if not file_name:
        file_name = 'certificado'

    root, ext = os.path.splitext(file_name)
    if ext:
        return file_name

    guessed_ext = mimetypes.guess_extension(content_type or '')
    if guessed_ext:
        return f"{root}{guessed_ext}"

    return f"{root}.bin"


def _detect_content_type_from_bytes(file_bytes):
    if not file_bytes:
        return None

    if file_bytes.startswith(b'%PDF-'):
        return 'application/pdf'
    if file_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if file_bytes.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if file_bytes.startswith((b'GIF87a', b'GIF89a')):
        return 'image/gif'
    if file_bytes[:4] == b'RIFF' and file_bytes[8:12] == b'WEBP':
        return 'image/webp'

    return None


def _resolve_content_type(content_type, file_name=None, file_bytes=None):
    normalized = (content_type or '').split(';', 1)[0].strip().lower()
    guessed_from_name, _ = mimetypes.guess_type(file_name or '')

    if not normalized or normalized == 'application/octet-stream':
        normalized = guessed_from_name or normalized

    if (not normalized or normalized == 'application/octet-stream') and file_bytes:
        sniffed_type = _detect_content_type_from_bytes(file_bytes)
        if sniffed_type:
            normalized = sniffed_type

    return normalized or 'application/octet-stream'


def _build_inline_file_response(file_obj_or_bytes, content_type, file_name, use_file_response=False):
    final_name = _ensure_extension(file_name, content_type)

    if use_file_response:
        response = FileResponse(
            file_obj_or_bytes,
            as_attachment=False,
            filename=final_name,
            content_type=content_type or 'application/octet-stream',
        )
    else:
        response = HttpResponse(
            file_obj_or_bytes,
            content_type=content_type or 'application/octet-stream',
        )

    response['Content-Disposition'] = f'inline; filename="{final_name}"'
    return response

@login_required
@internal_permission_required('admin.dashboard.view')
def panel_admin(request):

    clientes_pendientes = Cliente.objects.filter(estado_revision=Cliente.REVIEW_STATUS_PENDING).count()
    clientes_aprobados = Cliente.objects.filter(estado_revision=Cliente.REVIEW_STATUS_APPROVED).count()
    vendedores = Usuario.objects.filter(role='vendedor').count()
    productos = Producto.objects.count()

    context = {
        'clientes_pendientes': clientes_pendientes,
        'clientes_aprobados': clientes_aprobados,
        'vendedores': vendedores,
        'productos': productos,
        'quickbooks_status': get_connection_status(),
    }
    context.update(get_dashboard_sync_context(request=request))

    return render(request, 'admin/dashboard.html', context)


@login_required
def perfil_admin(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    usuario = request.user

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        if action == 'profile':
            nombre = (request.POST.get('nombre') or '').strip()
            apellido = (request.POST.get('apellido') or '').strip()
            email = (request.POST.get('email') or '').strip()

            if not email:
                messages.error(request, _('El correo electronico es obligatorio.'))
                return redirect('perfil_admin')

            existing_user = Usuario.objects.filter(email__iexact=email).exclude(pk=usuario.pk).first()
            if existing_user:
                messages.error(request, _('Ya existe otro usuario con ese correo electronico.'))
                return redirect('perfil_admin')

            usuario.first_name = nombre
            usuario.last_name = apellido
            usuario.email = email
            usuario.save(update_fields=['first_name', 'last_name', 'email'])

            messages.success(request, _('Tu perfil fue actualizado correctamente.'))
            return redirect('perfil_admin')

        if action == 'password':
            current_password = request.POST.get('current_password') or ''
            new_password = request.POST.get('new_password') or ''
            confirm_password = request.POST.get('confirm_password') or ''

            if not usuario.check_password(current_password):
                messages.error(request, _('La contrasena actual no es correcta.'))
                return redirect('perfil_admin')

            if not new_password or not confirm_password:
                messages.error(request, _('Debes ingresar y confirmar la nueva contrasena.'))
                return redirect('perfil_admin')

            if new_password != confirm_password:
                messages.error(request, _('Las nuevas contrasenas no coinciden.'))
                return redirect('perfil_admin')

            try:
                validate_password(new_password, usuario)
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, error)
                return redirect('perfil_admin')

            usuario.set_password(new_password)
            usuario.save(update_fields=['password'])
            update_session_auth_hash(request, usuario)

            messages.success(request, _('Tu contrasena fue actualizada correctamente.'))
            return redirect('perfil_admin')

    return render(request, 'admin/perfil_admin.html', {
        'usuario_objetivo': usuario,
    })


@login_required
@internal_permission_required('admin.content.view')
def contenido_home(request):

    context = {
        'marcas_activas': Marca.objects.filter(activo=True).count(),
        'marcas_inactivas': Marca.objects.filter(activo=False).count(),
        'testimonios_activos': Testimonio.objects.filter(activo=True).count(),
        'testimonios_inactivos': Testimonio.objects.filter(activo=False).count(),
        'home_contenido': _get_or_create_home_contenido(),
    }

    return render(request, 'admin/contenido_home.html', context)


@login_required
@internal_permission_required('admin.content.manage')
def editar_home_contenido(request):

    contenido = _get_or_create_home_contenido()

    if request.method == 'POST':
        migration_pending_warning = False

        contenido.hero_titulo_principal = (request.POST.get('hero_titulo_principal') or '').strip() or contenido.hero_titulo_principal
        contenido.hero_titulo_principal_en = (request.POST.get('hero_titulo_principal_en') or '').strip()

        contenido.hero_titulo_resaltado = (request.POST.get('hero_titulo_resaltado') or '').strip() or contenido.hero_titulo_resaltado
        contenido.hero_titulo_resaltado_en = (request.POST.get('hero_titulo_resaltado_en') or '').strip()

        contenido.hero_titulo_final = (request.POST.get('hero_titulo_final') or '').strip() or contenido.hero_titulo_final
        contenido.hero_titulo_final_en = (request.POST.get('hero_titulo_final_en') or '').strip()

        contenido.hero_subtitulo = (request.POST.get('hero_subtitulo') or '').strip() or contenido.hero_subtitulo
        contenido.hero_subtitulo_en = (request.POST.get('hero_subtitulo_en') or '').strip()

        contenido.hero_boton_texto = (request.POST.get('hero_boton_texto') or '').strip() or contenido.hero_boton_texto
        contenido.hero_boton_texto_en = (request.POST.get('hero_boton_texto_en') or '').strip()

        contenido.cta_titulo = (request.POST.get('cta_titulo') or '').strip() or contenido.cta_titulo
        contenido.cta_titulo_en = (request.POST.get('cta_titulo_en') or '').strip()

        contenido.cta_boton_registro_texto = (request.POST.get('cta_boton_registro_texto') or '').strip() or contenido.cta_boton_registro_texto
        contenido.cta_boton_registro_texto_en = (request.POST.get('cta_boton_registro_texto_en') or '').strip()

        contenido.cta_boton_catalogo_texto = (request.POST.get('cta_boton_catalogo_texto') or '').strip() or contenido.cta_boton_catalogo_texto
        contenido.cta_boton_catalogo_texto_en = (request.POST.get('cta_boton_catalogo_texto_en') or '').strip()

        contenido.quienes_titulo = (request.POST.get('quienes_titulo') or '').strip() or contenido.quienes_titulo
        contenido.quienes_titulo_en = (request.POST.get('quienes_titulo_en') or '').strip()

        contenido.quienes_descripcion = (request.POST.get('quienes_descripcion') or '').strip() or contenido.quienes_descripcion
        contenido.quienes_descripcion_en = (request.POST.get('quienes_descripcion_en') or '').strip()

        contenido.beneficio_1_titulo = (request.POST.get('beneficio_1_titulo') or '').strip() or contenido.beneficio_1_titulo
        contenido.beneficio_1_titulo_en = (request.POST.get('beneficio_1_titulo_en') or '').strip()
        contenido.beneficio_1_subtitulo = (request.POST.get('beneficio_1_subtitulo') or '').strip() or contenido.beneficio_1_subtitulo
        contenido.beneficio_1_subtitulo_en = (request.POST.get('beneficio_1_subtitulo_en') or '').strip()

        contenido.beneficio_2_titulo = (request.POST.get('beneficio_2_titulo') or '').strip() or contenido.beneficio_2_titulo
        contenido.beneficio_2_titulo_en = (request.POST.get('beneficio_2_titulo_en') or '').strip()
        contenido.beneficio_2_subtitulo = (request.POST.get('beneficio_2_subtitulo') or '').strip() or contenido.beneficio_2_subtitulo
        contenido.beneficio_2_subtitulo_en = (request.POST.get('beneficio_2_subtitulo_en') or '').strip()

        contenido.beneficio_3_titulo = (request.POST.get('beneficio_3_titulo') or '').strip() or contenido.beneficio_3_titulo
        contenido.beneficio_3_titulo_en = (request.POST.get('beneficio_3_titulo_en') or '').strip()
        contenido.beneficio_3_subtitulo = (request.POST.get('beneficio_3_subtitulo') or '').strip() or contenido.beneficio_3_subtitulo
        contenido.beneficio_3_subtitulo_en = (request.POST.get('beneficio_3_subtitulo_en') or '').strip()

        contenido.beneficio_4_titulo = (request.POST.get('beneficio_4_titulo') or '').strip() or contenido.beneficio_4_titulo
        contenido.beneficio_4_titulo_en = (request.POST.get('beneficio_4_titulo_en') or '').strip()
        contenido.beneficio_4_subtitulo = (request.POST.get('beneficio_4_subtitulo') or '').strip() or contenido.beneficio_4_subtitulo
        contenido.beneficio_4_subtitulo_en = (request.POST.get('beneficio_4_subtitulo_en') or '').strip()

        contenido.estadistica_1_valor = (request.POST.get('estadistica_1_valor') or '').strip() or contenido.estadistica_1_valor
        contenido.estadistica_1_valor_en = (request.POST.get('estadistica_1_valor_en') or '').strip()
        contenido.estadistica_1_label = (request.POST.get('estadistica_1_label') or '').strip() or contenido.estadistica_1_label
        contenido.estadistica_1_label_en = (request.POST.get('estadistica_1_label_en') or '').strip()

        contenido.estadistica_2_valor = (request.POST.get('estadistica_2_valor') or '').strip() or contenido.estadistica_2_valor
        contenido.estadistica_2_valor_en = (request.POST.get('estadistica_2_valor_en') or '').strip()
        contenido.estadistica_2_label = (request.POST.get('estadistica_2_label') or '').strip() or contenido.estadistica_2_label
        contenido.estadistica_2_label_en = (request.POST.get('estadistica_2_label_en') or '').strip()

        contenido.estadistica_3_valor = (request.POST.get('estadistica_3_valor') or '').strip() or contenido.estadistica_3_valor
        contenido.estadistica_3_valor_en = (request.POST.get('estadistica_3_valor_en') or '').strip()
        contenido.estadistica_3_label = (request.POST.get('estadistica_3_label') or '').strip() or contenido.estadistica_3_label
        contenido.estadistica_3_label_en = (request.POST.get('estadistica_3_label_en') or '').strip()

        contenido.footer_empresa_titulo = (request.POST.get('footer_empresa_titulo') or '').strip() or contenido.footer_empresa_titulo
        contenido.footer_empresa_titulo_en = (request.POST.get('footer_empresa_titulo_en') or '').strip()
        contenido.footer_empresa_descripcion = (request.POST.get('footer_empresa_descripcion') or '').strip() or contenido.footer_empresa_descripcion
        contenido.footer_empresa_descripcion_en = (request.POST.get('footer_empresa_descripcion_en') or '').strip()

        contenido.footer_contacto_titulo = (request.POST.get('footer_contacto_titulo') or '').strip() or contenido.footer_contacto_titulo
        contenido.footer_contacto_titulo_en = (request.POST.get('footer_contacto_titulo_en') or '').strip()
        contenido.footer_contacto_direccion_linea_1 = (request.POST.get('footer_contacto_direccion_linea_1') or '').strip() or contenido.footer_contacto_direccion_linea_1
        contenido.footer_contacto_direccion_linea_2 = (request.POST.get('footer_contacto_direccion_linea_2') or '').strip()
        contenido.footer_contacto_email = (request.POST.get('footer_contacto_email') or '').strip() or contenido.footer_contacto_email
        contenido.footer_contacto_telefono = (request.POST.get('footer_contacto_telefono') or '').strip() or contenido.footer_contacto_telefono

        contenido.activo = True if request.POST.get('activo') else False

        try:
            contenido.save()
        except (OperationalError, ProgrammingError):
            try:
                ensure_homecontenido_quienes_schema()
                contenido.save()
            except Exception:
                if contenido.pk:
                    legacy_update_fields = [
                        'hero_titulo_principal',
                        'hero_titulo_principal_en',
                        'hero_titulo_resaltado',
                        'hero_titulo_resaltado_en',
                        'hero_titulo_final',
                        'hero_titulo_final_en',
                        'hero_subtitulo',
                        'hero_subtitulo_en',
                        'hero_boton_texto',
                        'hero_boton_texto_en',
                        'cta_titulo',
                        'cta_titulo_en',
                        'cta_boton_registro_texto',
                        'cta_boton_registro_texto_en',
                        'cta_boton_catalogo_texto',
                        'cta_boton_catalogo_texto_en',
                        'activo',
                    ]
                    contenido.save(update_fields=legacy_update_fields)
                    messages.warning(
                        request,
                        _('La base de datos aun no tiene todos los campos nuevos del home. El resto del contenido se guardo, pero las secciones nuevas no podran guardarse hasta que Railway aplique la migracion.')
                    )
                    migration_pending_warning = True
                else:
                    raise

        cache.delete('home:contenido')
        if not migration_pending_warning:
            messages.success(request, _('Contenido del home actualizado correctamente'))
        return redirect('contenido_home')

    return render(request, 'admin/editar_home_contenido.html', {
        'contenido': contenido,
    })


@login_required
@internal_permission_required('admin.content.view')
def lista_testimonios(request):

    testimonios = Testimonio.objects.all()

    return render(request, 'admin/testimonios.html', {
        'testimonios': testimonios,
    })


@login_required
@internal_permission_required('admin.content.manage')
def crear_testimonio(request):

    if request.method == 'POST':
        nombre = (request.POST.get('nombre') or '').strip()
        negocio = (request.POST.get('negocio') or '').strip()
        negocio_en = (request.POST.get('negocio_en') or '').strip()
        comentario = (request.POST.get('comentario') or '').strip()
        comentario_en = (request.POST.get('comentario_en') or '').strip()
        foto = request.FILES.get('foto')

        try:
            estrellas = int(request.POST.get('estrellas') or 5)
        except ValueError:
            estrellas = 5

        try:
            orden = int(request.POST.get('orden') or 0)
        except ValueError:
            orden = 0

        if not nombre or not comentario:
            return render(request, 'admin/crear_testimonio.html', {
                'error': _('Nombre y comentario son obligatorios.'),
                'form_data': request.POST,
            })

        if estrellas < 1:
            estrellas = 1
        if estrellas > 5:
            estrellas = 5

        Testimonio.objects.create(
            nombre=nombre,
            negocio=negocio,
            negocio_en=negocio_en,
            comentario=comentario,
            comentario_en=comentario_en,
            estrellas=estrellas,
            foto=foto,
            orden=orden,
            activo=True if request.POST.get('activo') else False,
        )

        cache.delete('home:testimonios_activos')

        messages.success(request, _('Testimonio creado correctamente'))
        return redirect('lista_testimonios')

    return render(request, 'admin/crear_testimonio.html')


@login_required
@internal_permission_required('admin.content.manage')
def editar_testimonio(request, testimonio_id):

    testimonio = get_object_or_404(Testimonio, id=testimonio_id)

    if request.method == 'POST':
        nombre = (request.POST.get('nombre') or '').strip()
        negocio = (request.POST.get('negocio') or '').strip()
        negocio_en = (request.POST.get('negocio_en') or '').strip()
        comentario = (request.POST.get('comentario') or '').strip()
        comentario_en = (request.POST.get('comentario_en') or '').strip()
        foto = request.FILES.get('foto')

        try:
            estrellas = int(request.POST.get('estrellas') or 5)
        except ValueError:
            estrellas = 5

        try:
            orden = int(request.POST.get('orden') or 0)
        except ValueError:
            orden = 0

        if not nombre or not comentario:
            return render(request, 'admin/editar_testimonio.html', {
                'error': _('Nombre y comentario son obligatorios.'),
                'testimonio': testimonio,
            })

        if estrellas < 1:
            estrellas = 1
        if estrellas > 5:
            estrellas = 5

        testimonio.nombre = nombre
        testimonio.negocio = negocio
        testimonio.negocio_en = negocio_en
        testimonio.comentario = comentario
        testimonio.comentario_en = comentario_en
        testimonio.estrellas = estrellas
        testimonio.orden = orden
        testimonio.activo = True if request.POST.get('activo') else False
        if foto:
            if testimonio.foto:
                testimonio.foto.delete(save=False)
            testimonio.foto = foto
        testimonio.save()

        cache.delete('home:testimonios_activos')

        messages.success(request, _('Testimonio actualizado correctamente'))
        return redirect('lista_testimonios')

    return render(request, 'admin/editar_testimonio.html', {
        'testimonio': testimonio,
    })


@login_required
@internal_permission_required('admin.content.manage')
def desactivar_testimonio(request, testimonio_id):

    testimonio = get_object_or_404(Testimonio, id=testimonio_id)
    testimonio.activo = False
    testimonio.save(update_fields=['activo'])

    cache.delete('home:testimonios_activos')

    messages.success(request, _('Testimonio ocultado correctamente'))
    return redirect('lista_testimonios')


@login_required
@internal_permission_required('admin.content.manage')
def activar_testimonio(request, testimonio_id):

    testimonio = get_object_or_404(Testimonio, id=testimonio_id)
    testimonio.activo = True
    testimonio.save(update_fields=['activo'])

    cache.delete('home:testimonios_activos')

    messages.success(request, _('Testimonio activado correctamente'))
    return redirect('lista_testimonios')

@login_required
def crear_vendedor(request):

    return crear_usuario_interno(request, preset_role='vendedor')


@login_required
def crear_backoffice(request):

    return crear_usuario_interno(request, preset_role='backoffice')


@login_required
@internal_permission_required('admin.users.manage')
def crear_usuario_interno(request, preset_role=None):

    if request.method == 'POST':

        role = _resolve_internal_role(request.POST.get('role'), fallback=preset_role or '')
        username = (request.POST.get('username') or '').strip()
        email = (request.POST.get('email') or '').strip()
        password = request.POST.get('password') or ''
        nombre = (request.POST.get('nombre') or '').strip()
        apellido = (request.POST.get('apellido') or '').strip()
        telefono = (request.POST.get('telefono') or '').strip()

        if not role:
            messages.error(request, _('Select a role for the internal user.'))
            return redirect(request.path)

        permission_overrides = build_permission_overrides_for_role(role, request.POST.getlist('permissions'))

        if not username:
            messages.error(request, _('Username is required.'))
            return redirect(request.path)

        if Usuario.objects.filter(username=username).exists():
            messages.error(request, _('This username is already in use.'))
            return redirect(request.path)

        if Usuario.objects.filter(email__iexact=email).exists():
            messages.error(request, _('Another user already uses that email address.'))
            return redirect(request.path)

        if not password:
            messages.error(request, _('Password is required.'))
            return redirect(request.path)

        Usuario.objects.create(
            username=username,
            email=email,
            password=make_password(password),
            first_name=nombre,
            last_name=apellido,
            telefono=telefono,
            role=role,
            permission_overrides=permission_overrides,
            is_active=True
        )

        messages.success(request, _('%(role)s created successfully.') % {'role': _get_internal_role_label(role)})

        return redirect('lista_usuarios_internos')

    context = {
        'selected_role': _resolve_internal_role(preset_role or '', fallback=''),
        'role_choices': [(role, _get_internal_role_label(role)) for role in sorted(_get_allowed_internal_roles())],
        'is_role_locked': bool(preset_role),
        'role_permission_defaults': _get_internal_role_permission_defaults(),
    }
    context.update(_build_internal_permission_context(context['selected_role']))
    return render(request, 'admin/crear_usuario_interno.html', context)

@login_required
def lista_vendedores(request):

    return lista_usuarios_internos(request)


@login_required
@internal_permission_required('admin.users.view')
def lista_usuarios_internos(request):
    base_queryset = _internal_users_queryset()
    view_mode = str(request.GET.get('view') or 'active').strip().lower()
    if view_mode == 'deactivated':
        usuarios_internos = base_queryset.filter(is_active=False)
    else:
        view_mode = 'active'
        usuarios_internos = base_queryset.filter(is_active=True)

    for usuario in usuarios_internos:
        usuario.permission_summary_labels = get_permission_summary_labels(usuario)

    context = {
        'usuarios_internos': usuarios_internos,
        'view_mode': view_mode,
        'active_count': base_queryset.filter(is_active=True).count(),
        'deactivated_count': base_queryset.filter(is_active=False).count(),
    }

    return render(request, 'admin/usuarios_internos.html', context)


@login_required
@internal_permission_required('admin.customers.assign', 'backoffice.customers.assign')
def lista_asignacion_clientes_vendedores(request):
    query = str(request.GET.get('q') or '').strip()
    vendedores = (
        get_active_vendedores_queryset()
        .annotate(assigned_count=Count('asignaciones_clientes', distinct=True))
    )
    if query:
        vendedores = vendedores.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(email__icontains=query)
            | Q(username__icontains=query)
        )

    context = {
        'vendedores': vendedores,
        'search_query': query,
        'total_clientes_aprobados': Cliente.objects.filter(aprobado=True).count(),
    }
    return render(request, 'admin/asignacion_clientes_vendedores.html', context)


@login_required
@internal_permission_required('admin.customers.assign', 'backoffice.customers.assign')
def asignar_clientes_vendedor(request, vendedor_id):
    vendedor = get_object_or_404(Usuario, id=vendedor_id, role='vendedor')

    if request.method == 'POST':
        if request.POST.get('assign_all') == '1':
            count = assign_all_approved_clientes_to_vendedor(vendedor=vendedor, assigned_by=request.user)
            messages.success(
                request,
                _('All approved customers (%(count)s) were assigned to %(vendor)s.')
                % {'count': count, 'vendor': vendedor.get_full_name() or vendedor.username},
            )
            return redirect('asignar_clientes_vendedor', vendedor_id=vendedor.id)

        result = sync_vendedor_cliente_assignments(
            vendedor=vendedor,
            selected_cliente_ids=request.POST.getlist('cliente_ids'),
            assigned_by=request.user,
        )
        messages.success(
            request,
            _('Customer assignments updated for %(vendor)s: %(assigned)s assigned, %(unassigned)s unassigned.')
            % {
                'vendor': vendedor.get_full_name() or vendedor.username,
                'assigned': result['assigned_count'],
                'unassigned': result['unassigned_count'],
            },
        )
        return redirect('asignar_clientes_vendedor', vendedor_id=vendedor.id)

    query = str(request.GET.get('q') or '').strip()
    clientes = (
        Cliente.objects.filter(aprobado=True)
        .select_related('usuario', 'vendedor_asignado')
        .order_by('nombre_empresa', 'id')
    )
    if query:
        clientes = clientes.filter(
            Q(nombre_empresa__icontains=query)
            | Q(usuario__first_name__icontains=query)
            | Q(usuario__last_name__icontains=query)
            | Q(usuario__email__icontains=query)
            | Q(telefono__icontains=query)
        )

    from config.clientes.models import ClienteVendedorAsignacion

    assigned_ids = set(
        ClienteVendedorAsignacion.objects.filter(vendedor=vendedor).values_list('cliente_id', flat=True)
    )
    clientes = clientes.prefetch_related('asignaciones_vendedores__vendedor')

    context = {
        'vendedor': vendedor,
        'clientes': clientes,
        'assigned_ids': assigned_ids,
        'search_query': query,
        'assigned_count': len(assigned_ids),
        'total_approved_count': Cliente.objects.filter(aprobado=True).count(),
    }
    return render(request, 'admin/asignar_clientes_vendedor.html', context)


@login_required
def editar_vendedor(request, vendedor_id):

    return editar_usuario_interno(request, vendedor_id, preset_role='vendedor')


@login_required
def editar_backoffice(request, usuario_id):

    return editar_usuario_interno(request, usuario_id, preset_role='backoffice')


@login_required
@internal_permission_required('admin.users.manage')
def editar_usuario_interno(request, usuario_id, preset_role=None):

    filters = {'id': usuario_id, 'role__in': sorted(_get_allowed_internal_roles())}
    if preset_role:
        filters['role'] = preset_role

    usuario = get_object_or_404(Usuario, **filters)

    if request.method == 'POST':

        role = _resolve_internal_role(request.POST.get('role'), fallback=preset_role or usuario.role)
        username = (request.POST.get('username') or '').strip()
        email = (request.POST.get('email') or '').strip()
        telefono = (request.POST.get('telefono') or '').strip()
        nombre = (request.POST.get('nombre') or '').strip()
        apellido = (request.POST.get('apellido') or '').strip()
        password = request.POST.get('password') or ''
        confirm_password = request.POST.get('confirm_password') or ''
        permission_overrides = build_permission_overrides_for_role(role, request.POST.getlist('permissions'))

        if not username:
            messages.error(request, _('Username is required.'))
            return redirect(request.path)

        if Usuario.objects.filter(username=username).exclude(pk=usuario.pk).exists():
            messages.error(request, _('This username is already in use.'))
            return redirect(request.path)

        if Usuario.objects.filter(email__iexact=email).exclude(pk=usuario.pk).exists():
            messages.error(request, _('Another user already uses that email address.'))
            return redirect(request.path)

        if password or confirm_password:
            if not password or not confirm_password:
                messages.error(request, _('Enter and confirm the new password, or leave both fields blank.'))
                return redirect(request.path)
            if password != confirm_password:
                messages.error(request, _('The new passwords do not match.'))
                return redirect(request.path)
            try:
                validate_password(password, usuario)
            except ValidationError as exc:
                for error in exc.messages:
                    messages.error(request, error)
                return redirect(request.path)

        usuario.username = username
        usuario.first_name = nombre
        usuario.last_name = apellido
        usuario.email = email
        usuario.telefono = telefono
        usuario.role = role
        usuario.permission_overrides = permission_overrides
        update_fields = ['username', 'first_name', 'last_name', 'email', 'telefono', 'role', 'permission_overrides']
        if password:
            usuario.set_password(password)
            update_fields.append('password')
        usuario.save(update_fields=update_fields)

        success_message = _('%(role)s updated successfully.') % {'role': _get_internal_role_label(role)}
        if password:
            success_message = _('%(role)s updated successfully. Password changed.') % {'role': _get_internal_role_label(role)}
        messages.success(request, success_message)

        return redirect('lista_usuarios_internos')

    context = {
        'usuario_interno': usuario,
        'selected_role': _resolve_internal_role(preset_role or usuario.role),
        'role_choices': [(role, _get_internal_role_label(role)) for role in sorted(_get_allowed_internal_roles())],
        'is_role_locked': bool(preset_role),
    }
    context.update(_build_internal_permission_context(context['selected_role'], usuario.normalized_permission_overrides()))
    return render(request, 'admin/editar_usuario_interno.html', context)

@login_required
def desactivar_vendedor(request, vendedor_id):

    return desactivar_usuario_interno(request, vendedor_id, preset_role='vendedor')


@login_required
def desactivar_backoffice(request, usuario_id):

    return desactivar_usuario_interno(request, usuario_id, preset_role='backoffice')


@login_required
@internal_permission_required('admin.users.manage')
def desactivar_usuario_interno(request, usuario_id, preset_role=None):

    filters = {'id': usuario_id, 'role__in': sorted(_get_allowed_internal_roles())}
    if preset_role:
        filters['role'] = preset_role

    usuario = get_object_or_404(Usuario, **filters)
    usuario.is_active = False
    usuario.save(update_fields=['is_active'])

    messages.success(
        request,
        _('%(role)s %(name)s deactivated.')
        % {'role': _get_internal_role_label(usuario.role), 'name': usuario.first_name or usuario.username}
    )

    return redirect(f"{reverse('lista_usuarios_internos')}?view=deactivated")

@login_required
def activar_vendedor(request, vendedor_id):

    return activar_usuario_interno(request, vendedor_id, preset_role='vendedor')


@login_required
def activar_backoffice(request, usuario_id):

    return activar_usuario_interno(request, usuario_id, preset_role='backoffice')


@login_required
@internal_permission_required('admin.users.manage')
def activar_usuario_interno(request, usuario_id, preset_role=None):

    filters = {'id': usuario_id, 'role__in': sorted(_get_allowed_internal_roles())}
    if preset_role:
        filters['role'] = preset_role

    usuario = get_object_or_404(Usuario, **filters)
    usuario.is_active = True
    usuario.save(update_fields=['is_active'])

    messages.success(
        request,
        _('%(role)s %(name)s activated.')
        % {'role': _get_internal_role_label(usuario.role), 'name': usuario.first_name or usuario.username}
    )

    return redirect('lista_usuarios_internos')

CUSTOMER_REQUESTS_PAGE_SIZE = 50


def _customer_requests_filter_params(request):
    params = {}
    query = str(request.GET.get('q') or '').strip()
    if query:
        params['q'] = query
    return params


def _customer_requests_queryset(request, view_mode):
    queryset = (
        Cliente.objects.select_related('usuario', 'rechazado_por', 'aprobado_por')
        .order_by('-creado_en')
    )

    if view_mode == 'rejected':
        queryset = queryset.filter(estado_revision=Cliente.REVIEW_STATUS_REJECTED)
    elif view_mode == 'approved':
        queryset = queryset.filter(estado_revision=Cliente.REVIEW_STATUS_APPROVED)
    else:
        queryset = queryset.filter(estado_revision=Cliente.REVIEW_STATUS_PENDING)

    query = _customer_requests_filter_params(request).get('q')
    if query:
        queryset = queryset.filter(
            Q(nombre_empresa__icontains=query)
            | Q(usuario__first_name__icontains=query)
            | Q(usuario__last_name__icontains=query)
            | Q(usuario__username__icontains=query)
        )
    return queryset


@login_required
@internal_permission_required('admin.customer_requests.view')
def clientes_pendientes(request):

    view_mode = (request.GET.get('view') or 'pending').strip().lower()
    if view_mode not in ('rejected', 'approved'):
        view_mode = 'pending'

    base_queryset = Cliente.objects.all()
    filter_params = _customer_requests_filter_params(request)
    paginator = Paginator(_customer_requests_queryset(request, view_mode), CUSTOMER_REQUESTS_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'clientes': page_obj.object_list,
        'page_obj': page_obj,
        'view_mode': view_mode,
        'filter_q': filter_params.get('q', ''),
        'pending_count': base_queryset.filter(estado_revision=Cliente.REVIEW_STATUS_PENDING).count(),
        'rejected_count': base_queryset.filter(estado_revision=Cliente.REVIEW_STATUS_REJECTED).count(),
        'approved_count': base_queryset.filter(estado_revision=Cliente.REVIEW_STATUS_APPROVED).count(),
        'price_tier_choices': Cliente.PRICE_TIER_CHOICES,
    }

    return render(request, 'admin/clientes_pendientes.html', context)

@login_required
@internal_permission_required('admin.customer_requests.manage')
def aprobar_cliente(request, cliente_id):

    if request.method != 'POST':
        messages.error(request, _('Debes enviar la aprobacion desde el formulario del administrador.'))
        return redirect('ver_cliente', cliente_id=cliente_id)

    cliente = get_object_or_404(Cliente, id=cliente_id)
    if cliente.estado_revision == Cliente.REVIEW_STATUS_APPROVED:
        messages.info(request, _('Use the pricing update action to change prices for approved customers.'))
        return redirect('ver_cliente', cliente_id=cliente_id)

    redirect_view = (request.POST.get('view') or 'pending').strip().lower()
    nivel_precio = normalize_price_tier(request.POST.get('nivel_precio'), Cliente.PRICE_TIER_UNASSIGNED)

    cliente.aprobado = True
    cliente.estado_revision = Cliente.REVIEW_STATUS_APPROVED
    cliente.aprobado_en = timezone.now()
    cliente.aprobado_por = request.user
    cliente.nivel_precio = nivel_precio
    cliente.save(update_fields=['aprobado', 'estado_revision', 'aprobado_en', 'aprobado_por', 'nivel_precio'])

    # activar usuario
    usuario = cliente.usuario
    usuario.is_active = True
    usuario.save(update_fields=['is_active'])

    try:
        email_sent = _send_client_decision_email(
            client_email=usuario.email,
            client_name=(usuario.first_name or usuario.username or 'Client').strip(),
            approved=True,
            company_name=cliente.nombre_empresa,
        )
        if email_sent == 'sent':
            messages.success(request, _('Cliente aprobado y correo enviado al cliente.'))
        else:
            messages.success(request, _('Cliente aprobado. El cliente no tiene correo registrado para notificar.'))
    except RuntimeError as exc:
        logger.warning('Configuracion de correo incompleta al aprobar cliente %s: %s', cliente.id, exc)
        messages.warning(request, _('Cliente aprobado, pero el correo no esta configurado en este entorno. Falta RESEND_API_KEY.'))
    except Exception as exc:
        logger.exception('Error enviando correo de aprobación para cliente %s: %s', cliente.id, exc)
        messages.warning(request, _('Cliente aprobado, pero no se pudo enviar el correo al cliente.'))

    return redirect(f"{reverse('clientes_pendientes')}?view={redirect_view}")


@login_required
@internal_permission_required('admin.customer_requests.manage')
def actualizar_precio_cliente(request, cliente_id):

    if request.method != 'POST':
        messages.error(request, _('Debes actualizar el precio desde el formulario del administrador.'))
        return redirect('ver_cliente', cliente_id=cliente_id)

    cliente = get_object_or_404(Cliente, id=cliente_id)
    redirect_view = (request.POST.get('view') or '').strip().lower()
    if cliente.estado_revision != Cliente.REVIEW_STATUS_APPROVED:
        messages.error(request, _('Solo puedes actualizar precios para clientes aprobados.'))
        return redirect('ver_cliente', cliente_id=cliente_id)

    nivel_precio = normalize_price_tier(request.POST.get('nivel_precio'), Cliente.PRICE_TIER_UNASSIGNED)
    cliente.nivel_precio = nivel_precio
    cliente.save(update_fields=['nivel_precio'])

    messages.success(request, _('Customer pricing updated successfully.'))
    if redirect_view == 'approved':
        return redirect(f"{reverse('clientes_pendientes')}?view=approved")
    return redirect('ver_cliente', cliente_id=cliente_id)

@login_required
@internal_permission_required('admin.customer_requests.manage')
def rechazar_cliente(request, cliente_id):

    if request.method != 'POST':
        messages.error(request, _('Debes enviar el rechazo desde el formulario del administrador.'))
        return redirect('ver_cliente', cliente_id=cliente_id)

    cliente = get_object_or_404(Cliente, id=cliente_id)
    redirect_view = (request.POST.get('view') or 'rejected').strip().lower()
    rejection_reason = (request.POST.get('nota_rechazo') or '').strip()

    if not rejection_reason:
        messages.error(request, _('Debes escribir una nota explicando por que se rechazo la solicitud.'))
        return redirect(f"{reverse('ver_cliente', args=[cliente.id])}?view={redirect_view}")

    usuario = cliente.usuario
    rejection_example = request.FILES.get('adjunto_rechazo')

    cliente.aprobado = False
    cliente.estado_revision = Cliente.REVIEW_STATUS_REJECTED
    cliente.nota_rechazo = rejection_reason
    if rejection_example:
        cliente.adjunto_rechazo = rejection_example
    cliente.rechazado_en = timezone.now()
    cliente.rechazado_por = request.user
    cliente.correction_requested_at = timezone.now()

    update_fields = [
        'aprobado',
        'estado_revision',
        'nota_rechazo',
        'rechazado_en',
        'rechazado_por',
        'correction_requested_at',
    ]
    if rejection_example:
        update_fields.append('adjunto_rechazo')

    cliente.save(update_fields=update_fields)
    usuario.is_active = False
    usuario.save(update_fields=['is_active'])

    client_email = usuario.email
    client_name = (usuario.first_name or usuario.username or 'Client').strip()
    company_name = cliente.nombre_empresa
    correction_url = _build_client_correction_url(request, cliente)

    try:
        email_sent = _send_client_decision_email(
            client_email=client_email,
            client_name=client_name,
            approved=False,
            company_name=company_name,
            rejection_reason=rejection_reason,
            correction_url=correction_url,
            rejection_example=cliente.adjunto_rechazo,
        )
        if email_sent == 'sent':
            messages.success(request, _('Cliente rechazado y correo enviado al cliente.'))
        else:
            messages.success(request, _('Cliente rechazado. El cliente no tiene correo registrado para notificar.'))
    except RuntimeError as exc:
        logger.warning('Configuracion de correo incompleta al rechazar cliente %s: %s', cliente.id, exc)
        messages.warning(request, _('Cliente rechazado, pero el correo no esta configurado en este entorno. Falta RESEND_API_KEY.'))
    except Exception as exc:
        logger.exception('Error enviando correo de rechazo para cliente %s: %s', cliente.id, exc)
        messages.warning(request, _('Cliente rechazado, pero no se pudo enviar el correo al cliente.'))

    return redirect(f"{reverse('clientes_pendientes')}?view={redirect_view}")

@login_required
@internal_permission_required('admin.customer_requests.view')
def ver_cliente(request, cliente_id):

    cliente = get_object_or_404(
        Cliente.objects.select_related('usuario', 'vendedor_asignado'),
        id=cliente_id,
    )

    context = {
        'cliente': cliente,
        'view_mode': (request.GET.get('view') or 'pending').strip().lower(),
        'price_tier_choices': Cliente.PRICE_TIER_CHOICES,
        'vendedores_activos': get_active_vendedores_queryset(),
    }

    return render(request, 'admin/ver_cliente.html', context)


@login_required
@xframe_options_sameorigin
@internal_permission_required('admin.customer_requests.view')
def ver_certificado_cliente(request, cliente_id):

    cliente = get_object_or_404(Cliente, id=cliente_id)

    if not cliente.certificado_tax:
        raise Http404("No hay certificado para este cliente")

    if request.GET.get('contenido') != '1':
        nombre_archivo = os.path.basename(cliente.certificado_tax.name) or f"certificado_{cliente.id}"
        extension = os.path.splitext(nombre_archivo)[1].lower()
        context = {
            'cliente': cliente,
            'nombre_archivo': nombre_archivo,
            'certificado_src': f"{request.path}?contenido=1",
            'es_pdf': extension == '.pdf',
        }
        return render(request, 'admin/ver_certificado.html', context)

    certificado = cliente.certificado_tax
    nombre_archivo = os.path.basename(certificado.name) or f"certificado_{cliente.id}"
    diagnostico = [
        f"cliente_id={cliente.id}",
        f"certificado_tax.name={certificado.name}",
        f"nombre_archivo={nombre_archivo}",
    ]

    try:
        diagnostico.append(f"certificado_tax.url={certificado.url}")
    except Exception as exc:
        diagnostico.append(f"certificado_tax.url=ERROR: {exc}")

    try:
        certificado.open('rb')
        head_bytes = b''
        try:
            head_bytes = certificado.read(32)
            certificado.seek(0)
        except Exception:
            pass

        content_type = _resolve_content_type(None, nombre_archivo, head_bytes)
        return _build_inline_file_response(
            certificado,
            content_type,
            nombre_archivo,
            use_file_response=True,
        )
    except Exception as exc:
        logger.warning("Fallo open() para certificado cliente %s: %s", cliente.id, exc)
        diagnostico.append(f"open() fallo: {exc}")

        download_url, resolved_name = _cloudinary_download_url_for_file(cliente.certificado_tax)
        if download_url:
            diagnostico.append(f"cloudinary api/url resolver ok: {download_url}")
            try:
                with urllib.request.urlopen(download_url, timeout=20) as remote_file:
                    file_bytes = remote_file.read()
                    remote_content_type = remote_file.headers.get_content_type()
                resolved_content_type = _resolve_content_type(
                    remote_content_type,
                    resolved_name or nombre_archivo,
                    file_bytes,
                )
                return _build_inline_file_response(
                    file_bytes,
                    resolved_content_type,
                    resolved_name or nombre_archivo,
                )
            except Exception as download_exc:
                logger.warning("Fallo descarga Cloudinary cliente %s: %s", cliente.id, download_exc)
                diagnostico.append(f"descarga cloudinary resolver fallo: {download_exc}")

                pdf_preview = _cloudinary_pdf_preview(cliente.certificado_tax)
                if pdf_preview:
                    file_bytes, remote_content_type, final_name = pdf_preview
                    diagnostico.append("pdf preview cloudinary: ok")
                    return _build_inline_file_response(file_bytes, remote_content_type, final_name)
                diagnostico.append("pdf preview cloudinary: sin coincidencia")
        else:
            diagnostico.append("cloudinary api/url resolver: sin coincidencia")

        try:
            fallback_url = cliente.certificado_tax.url
            if fallback_url:
                diagnostico.append(f"fallback url intento: {fallback_url}")
                with urllib.request.urlopen(fallback_url, timeout=20) as remote_file:
                    file_bytes = remote_file.read()
                    remote_content_type = remote_file.headers.get_content_type()
                fallback_content_type = _resolve_content_type(remote_content_type, nombre_archivo, file_bytes)
                return _build_inline_file_response(file_bytes, fallback_content_type, nombre_archivo)
        except Exception as fallback_exc:
            diagnostico.append(f"fallback url fallo: {fallback_exc}")

        probed_file = _probe_cloudinary_certificate(cliente.certificado_tax)
        if probed_file:
            file_bytes, remote_content_type, final_name = probed_file
            return _build_inline_file_response(file_bytes, remote_content_type, final_name)

        diagnostico.append("probe cloudinary: sin coincidencia")

        context = {
            'cliente': cliente,
            'diagnostico': diagnostico,
        }
        return render(request, 'admin/certificado_diagnostico.html', context, status=404)

#funcion del login
def login_view(request):

    next_url = request.POST.get('next') or request.GET.get('next') or ''
    invalid_credentials_message = _('Invalid username or password.')

    if request.method == 'POST':

        username = (request.POST.get('username') or '').lower()
        password = request.POST.get('password')
        
        # Detectar si es una petición AJAX (para cargar en modal)
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        # Verificar si el usuario existe
        try:
            user_exists = Usuario.objects.get(username=username)
            if not user_exists.is_active:
                inactive_message = _('This account is inactive. Contact an administrator.')
                if is_ajax:
                    return JsonResponse({'success': False, 'error': 'account_inactive', 'message': inactive_message})
                messages.error(request, inactive_message)
            else:
                # Usuario existe, verificar contraseña
                user = authenticate(request, username=username, password=password)
                
                if user is not None:
                    _clear_pending_messages(request)
                    login(request, user)
                    redirect_url = _resolve_login_redirect(user, next_url)

                    if is_ajax:
                        # Retornar JSON con éxito y la URL de redirección
                        return JsonResponse({'success': True, 'redirect': redirect_url})
                    else:
                        # Redirecciones normales
                        return redirect(redirect_url)
                else:
                    if is_ajax:
                        return JsonResponse({
                            'success': False,
                            'error': 'invalid_credentials',
                            'message': invalid_credentials_message,
                        })
                    messages.error(request, invalid_credentials_message)
        
        except Usuario.DoesNotExist:
            if is_ajax:
                return JsonResponse({
                    'success': False,
                    'error': 'invalid_credentials',
                    'message': invalid_credentials_message,
                })
            messages.error(request, invalid_credentials_message)

    # Detectar si es una petición AJAX (para cargar en modal)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    if is_ajax:
        return render(request, 'usuarios/login_modal_form.html', {'next_url': next_url})
    else:
        return render(request, 'usuarios/login.html', {'next_url': next_url})


# Función de logout personalizada
def logout_view(request):
    logout(request)
    return redirect(f"{reverse('home')}?no_back=1")


# Verificar si el username existe
def verificar_username(request):
    if request.method == 'POST':
        username = request.POST.get('username', '')
        existe = Usuario.objects.filter(username=username).exists()
        return JsonResponse({'existe': existe})
    return JsonResponse({'error': _('Método no permitido')}, status=400)


#funcion del registro
def registro_view(request):

    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password1')
        password2 = request.POST.get('password2')

        if Usuario.objects.filter(username=username).exists():
            message = _("El nombre de usuario ya existe")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'username_exists', 'message': message}, status=400)
            messages.error(request, message)
            return redirect('registro')

        if password != password2:
            message = _("Las contraseñas no coinciden")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'password_mismatch', 'message': message}, status=400)
            messages.error(request, message)
            return redirect('registro')

        nombre = request.POST.get('nombre')
        apellido = request.POST.get('apellido')
        telefono = request.POST.get('telefono')
        documento = request.POST.get('id_cliente')
        certificado = request.FILES.get('certificado')
        submitted_state = request.POST.get('estado', '').strip()
        submitted_city = request.POST.get('ciudad', '').strip()

        if not _is_valid_registration_document(documento):
            message = _("El ID personal o Business ID debe tener 8 o 9 dígitos.")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'invalid_document', 'message': message}, status=400)
            messages.error(request, message)
            return redirect('registro')

        if not submitted_state:
            message = _("Debes ingresar un estado o departamento.")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'invalid_state', 'message': message}, status=400)
            messages.error(request, message)
            return redirect('registro')

        if not submitted_city:
            message = _("Debes ingresar una ciudad.")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'invalid_city', 'message': message}, status=400)
            messages.error(request, message)
            return redirect('registro')

        if not certificado:
            message = _("Debes adjuntar el certificado tax para completar el registro.")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'certificado_required', 'message': message}, status=400)
            messages.error(request, message)
            return redirect('registro')

        if not request.POST.get('confirmacion'):
            message = _("Debes aceptar la declaración sobre la veracidad de la información fiscal para completar el registro.")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'tax_declaration_required', 'message': message}, status=400)
            messages.error(request, message)
            return redirect('registro')

        try:
            with transaction.atomic():
                usuario = Usuario.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    first_name=nombre,
                    last_name=apellido
                )

                usuario.telefono = telefono
                usuario.documento = documento
                usuario.role = 'cliente'
                usuario.is_active = False
                usuario.save()

                Cliente.objects.create(
                    usuario=usuario,
                    nombre_empresa=request.POST.get('empresa'),
                    telefono=request.POST.get('telefono_comercial'),
                    direccion=request.POST.get('direccion'),
                    ciudad=submitted_city,
                    estado=submitted_state,
                    codigo_postal=request.POST.get('codigo_postal'),
                    pais=request.POST.get('pais'),
                    sales_tax_number=request.POST.get('sales_tax'),
                    certificado_tax=certificado,
                    declaracion_fiscal_aceptada=True,
                    declaracion_fiscal_aceptada_en=timezone.now(),
                    estado_revision=Cliente.REVIEW_STATUS_PENDING,
                )

                # asegurar que el grupo exista
                grupo, created = Group.objects.get_or_create(name='Cliente')
                usuario.groups.add(grupo)
                
        except Exception as e:
            logger.error(f"Error en registro_view para usuario {username}: {str(e)}", exc_info=True)
            # A pesar del error, si el usuario se creó, mostramos éxito
            if Usuario.objects.filter(username=username).exists():
                message = _("Tu solicitud fue enviada. Un administrador revisará tu cuenta.")
                if is_ajax:
                    return JsonResponse({'success': True, 'message': message})
                return render(request, 'usuarios/login.html', {'registration_notice': message})
            else:
                # Si no se creó el usuario, mostrar error
                message = _("No se pudo completar el registro. Verifica los datos e intenta nuevamente.")
                if is_ajax:
                    return JsonResponse({'success': False, 'error': 'server_error', 'message': message}, status=400)
                messages.error(request, message)
                return redirect('registro')

        success_message = _("Tu solicitud fue enviada. Un administrador revisará tu cuenta.")

        if is_ajax:
            return JsonResponse({'success': True, 'message': success_message})

        return render(request, 'usuarios/login.html', {'registration_notice': success_message})

    return render(request, 'usuarios/registro.html', _registration_context())


def corregir_solicitud_cliente(request, correction_token):
    cliente = get_object_or_404(Cliente.objects.select_related('usuario'), correction_token=correction_token)

    if cliente.estado_revision != Cliente.REVIEW_STATUS_REJECTED:
        return render(request, 'usuarios/corregir_solicitud.html', {
            'cliente': cliente,
            'correction_available': False,
        })

    if request.method == 'POST':
        submitted_state = (request.POST.get('estado') or '').strip()
        submitted_city = (request.POST.get('ciudad') or '').strip()

        if not submitted_state or not submitted_city:
            messages.error(request, _('Debes completar ciudad y estado para reenviar la solicitud.'))
            return render(request, 'usuarios/corregir_solicitud.html', {
                'cliente': cliente,
                'correction_available': True,
            })

        certificado_actualizado = request.FILES.get('certificado')

        cliente.nombre_empresa = (request.POST.get('empresa') or '').strip()
        cliente.telefono = (request.POST.get('telefono_comercial') or '').strip()
        cliente.direccion = (request.POST.get('direccion') or '').strip()
        cliente.ciudad = submitted_city
        cliente.estado = submitted_state
        cliente.codigo_postal = (request.POST.get('codigo_postal') or '').strip()
        cliente.pais = (request.POST.get('pais') or '').strip() or cliente.pais
        cliente.sales_tax_number = (request.POST.get('sales_tax') or '').strip()
        if certificado_actualizado:
            cliente.certificado_tax = certificado_actualizado

        cliente.aprobado = False
        cliente.estado_revision = Cliente.REVIEW_STATUS_PENDING
        cliente.corrected_at = timezone.now()

        update_fields = [
            'nombre_empresa',
            'telefono',
            'direccion',
            'ciudad',
            'estado',
            'codigo_postal',
            'pais',
            'sales_tax_number',
            'aprobado',
            'estado_revision',
            'corrected_at',
        ]
        if certificado_actualizado:
            update_fields.append('certificado_tax')

        cliente.save(update_fields=update_fields)

        return render(request, 'usuarios/corregir_solicitud.html', {
            'cliente': cliente,
            'correction_available': False,
            'correction_success': True,
        })

    return render(request, 'usuarios/corregir_solicitud.html', {
        'cliente': cliente,
        'correction_available': True,
    })


def login_form_modal(request):
    """Devuelve solo el formulario de login para cargar en modal"""
    from django.http import HttpResponse
    import json

    invalid_credentials_message = str(_('Invalid username or password.'))
    
    if request.method == 'POST':
        username = (request.POST.get('username') or '').lower()
        password = request.POST.get('password', '')
        
        try:
            user_exists = Usuario.objects.get(username=username)
        except Usuario.DoesNotExist:
            return HttpResponse(
                json.dumps({'success': False, 'error': 'invalid_credentials', 'message': invalid_credentials_message}),
                content_type='application/json'
            )

        if not user_exists.is_active:
            return HttpResponse(
                json.dumps({
                    'success': False,
                    'error': 'account_inactive',
                    'message': str(_('This account is inactive. Contact an administrator.')),
                }),
                content_type='application/json'
            )

        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            redirect_url = _redirect_for_user(user)
            
            return HttpResponse(
                json.dumps({'success': True, 'redirect': redirect_url}),
                content_type='application/json'
            )

        return HttpResponse(
            json.dumps({'success': False, 'error': 'invalid_credentials', 'message': invalid_credentials_message}),
            content_type='application/json'
        )
    
    # GET - Devolver solo el formulario
    return render(request, 'usuarios/login_modal.html')


def password_reset_form_modal(request):
    """Devuelve y procesa el formulario de recuperacion para usarlo dentro del modal del home."""
    if request.method == 'POST':
        form = CustomerPasswordResetForm(request.POST)
        if form.is_valid():
            form.save(
                request=request,
                use_https=request.is_secure(),
                from_email=settings.DEFAULT_FROM_EMAIL or settings.SERVER_EMAIL or None,
                email_template_name='emails/password_reset_email.txt',
                html_email_template_name='emails/password_reset_email.html',
                subject_template_name='emails/password_reset_subject.txt',
            )
            html = render_to_string('usuarios/password_reset_modal_done.html', request=request)
            return JsonResponse({'success': True, 'html': html})

        html = render_to_string(
            'usuarios/password_reset_modal_form.html',
            {'form': form},
            request=request,
        )
        return JsonResponse({'success': False, 'html': html}, status=400)

    form = CustomerPasswordResetForm()
    return render(request, 'usuarios/password_reset_modal_form.html', {'form': form})


def _get_password_reset_user(uidb64):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        return Usuario._default_manager.get(pk=uid)
    except (TypeError, ValueError, OverflowError, Usuario.DoesNotExist):
        return None


def password_reset_confirm_modal(request, uidb64, token):
    """Procesa el cambio de contrasena dentro del modal del home usando el token del correo."""
    user = _get_password_reset_user(uidb64)
    validlink = bool(user and default_token_generator.check_token(user, token))

    if not validlink:
        context = {'validlink': False, 'form': None}
        if request.method == 'POST':
            html = render_to_string('usuarios/password_reset_modal_confirm.html', context, request=request)
            return JsonResponse({'success': False, 'html': html}, status=400)
        return render(request, 'usuarios/password_reset_modal_confirm.html', context)

    if request.method == 'POST':
        form = CustomerSetPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            html = render_to_string('usuarios/password_reset_modal_complete.html', request=request)
            return JsonResponse({'success': True, 'html': html})
        context = {'validlink': True, 'form': form}
        html = render_to_string('usuarios/password_reset_modal_confirm.html', context, request=request)
        return JsonResponse({'success': False, 'html': html}, status=400)

    form = CustomerSetPasswordForm(user)
    return render(
        request,
        'usuarios/password_reset_modal_confirm.html',
        {'validlink': True, 'form': form},
    )


def registro_form_modal(request):
    """Devuelve solo el formulario de registro para cargar en modal"""
    # GET - Devolver solo el formulario de registro
    return render(request, 'usuarios/registro_modal_form.html', _registration_context())


