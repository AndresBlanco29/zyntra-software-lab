from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.models import Group
from .models import Usuario
from config.clientes.models import Cliente
from config.core.models import Testimonio, HomeContenido
from config.productos.models import Producto, Marca
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import make_password
from django.http import JsonResponse, FileResponse, Http404, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext as _
from django.db import transaction
import mimetypes
import os
import re
import urllib.request
import urllib.error
import logging

logger = logging.getLogger(__name__)


def _get_or_create_home_contenido():
    contenido = HomeContenido.objects.order_by('-actualizado').first()
    if contenido is None:
        contenido = HomeContenido.objects.create(activo=True)
    return contenido


def _is_admin_user(user):
    return bool(user and user.is_authenticated and (user.is_superuser or user.role == 'admin'))


def _redirect_for_user(user):
    if _is_admin_user(user):
        return reverse('panel_admin')
    if user.role == 'vendedor':
        return reverse('vendedores_clientes')
    if user.role == 'cliente':
        return reverse('catalogo')
    return '/'


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
def panel_admin(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    clientes_pendientes = Cliente.objects.filter(aprobado=False).count()
    clientes_aprobados = Cliente.objects.filter(aprobado=True).count()
    vendedores = Usuario.objects.filter(role='vendedor').count()
    productos = Producto.objects.count()

    context = {
        'clientes_pendientes': clientes_pendientes,
        'clientes_aprobados': clientes_aprobados,
        'vendedores': vendedores,
        'productos': productos
    }

    return render(request, 'admin/dashboard.html', context)


@login_required
def contenido_home(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    context = {
        'marcas_activas': Marca.objects.filter(activo=True).count(),
        'marcas_inactivas': Marca.objects.filter(activo=False).count(),
        'testimonios_activos': Testimonio.objects.filter(activo=True).count(),
        'testimonios_inactivos': Testimonio.objects.filter(activo=False).count(),
        'home_contenido': _get_or_create_home_contenido(),
    }

    return render(request, 'admin/contenido_home.html', context)


@login_required
def editar_home_contenido(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    contenido = _get_or_create_home_contenido()

    if request.method == 'POST':
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

        contenido.activo = True if request.POST.get('activo') else False
        contenido.save()

        cache.delete('home:contenido')
        messages.success(request, 'Banners y textos del home actualizados correctamente')
        return redirect('contenido_home')

    return render(request, 'admin/editar_home_contenido.html', {
        'contenido': contenido,
    })


@login_required
def lista_testimonios(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    testimonios = Testimonio.objects.all()

    return render(request, 'admin/testimonios.html', {
        'testimonios': testimonios,
    })


@login_required
def crear_testimonio(request):

    if not _is_admin_user(request.user):
        return redirect('login')

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
                'error': 'Nombre y comentario son obligatorios.',
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

        messages.success(request, 'Testimonio creado correctamente')
        return redirect('lista_testimonios')

    return render(request, 'admin/crear_testimonio.html')


@login_required
def editar_testimonio(request, testimonio_id):

    if not _is_admin_user(request.user):
        return redirect('login')

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
                'error': 'Nombre y comentario son obligatorios.',
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

        messages.success(request, 'Testimonio actualizado correctamente')
        return redirect('lista_testimonios')

    return render(request, 'admin/editar_testimonio.html', {
        'testimonio': testimonio,
    })


@login_required
def desactivar_testimonio(request, testimonio_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    testimonio = get_object_or_404(Testimonio, id=testimonio_id)
    testimonio.activo = False
    testimonio.save(update_fields=['activo'])

    cache.delete('home:testimonios_activos')

    messages.success(request, 'Testimonio ocultado correctamente')
    return redirect('lista_testimonios')


@login_required
def activar_testimonio(request, testimonio_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    testimonio = get_object_or_404(Testimonio, id=testimonio_id)
    testimonio.activo = True
    testimonio.save(update_fields=['activo'])

    cache.delete('home:testimonios_activos')

    messages.success(request, 'Testimonio activado correctamente')
    return redirect('lista_testimonios')

@login_required
def crear_vendedor(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    if request.method == 'POST':

        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        nombre = request.POST.get('nombre')
        apellido = request.POST.get('apellido')
        telefono = request.POST.get('telefono')

        if Usuario.objects.filter(username=username).exists():
            messages.error(request, "El usuario ya existe")
            return redirect('crear_vendedor')

        Usuario.objects.create(
            username=username,
            email=email,
            password=make_password(password),
            first_name=nombre,
            last_name=apellido,
            telefono=telefono,
            role='vendedor',
            is_active=True
        )

        messages.success(request, "Vendedor creado correctamente")

        return redirect('lista_vendedores')

    return render(request, 'admin/crear_vendedor.html')

@login_required
def lista_vendedores(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    vendedores = Usuario.objects.filter(role='vendedor')

    context = {
        'vendedores': vendedores
    }

    return render(request, 'admin/vendedores.html', context)

@login_required
def editar_vendedor(request, vendedor_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    vendedor = get_object_or_404(Usuario, id=vendedor_id, role='vendedor')

    if request.method == 'POST':

        vendedor.first_name = request.POST.get('nombre')
        vendedor.last_name = request.POST.get('apellido')
        vendedor.email = request.POST.get('email')
        vendedor.telefono = request.POST.get('telefono')
        vendedor.save()

        messages.success(request, "Vendedor actualizado correctamente")

        return redirect('lista_vendedores')

    context = {
        'vendedor': vendedor
    }

    return render(request, 'admin/editar_vendedor.html', context)

@login_required
def desactivar_vendedor(request, vendedor_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    vendedor = get_object_or_404(Usuario, id=vendedor_id, role='vendedor')
    vendedor.is_active = False
    vendedor.save()

    messages.success(request, f"Vendedor {vendedor.first_name} desactivado")

    return redirect('lista_vendedores')

@login_required
def activar_vendedor(request, vendedor_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    vendedor = get_object_or_404(Usuario, id=vendedor_id, role='vendedor')
    vendedor.is_active = True
    vendedor.save()

    messages.success(request, f"Vendedor {vendedor.first_name} activado")

    return redirect('lista_vendedores')

@login_required
def clientes_pendientes(request):

    if not _is_admin_user(request.user):
        return redirect('login')

    clientes = Cliente.objects.filter(aprobado=False)

    context = {
        'clientes': clientes
    }

    return render(request, 'admin/clientes_pendientes.html', context)

@login_required
def aprobar_cliente(request, cliente_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    cliente = get_object_or_404(Cliente, id=cliente_id)

    cliente.aprobado = True
    cliente.save()

    # activar usuario
    usuario = cliente.usuario
    usuario.is_active = True
    usuario.save()

    return redirect('clientes_pendientes')

@login_required
def rechazar_cliente(request, cliente_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    cliente = get_object_or_404(Cliente, id=cliente_id)

    usuario = cliente.usuario

    cliente.delete()
    usuario.delete()

    return redirect('clientes_pendientes')

@login_required
def ver_cliente(request, cliente_id):

    if not _is_admin_user(request.user):
        return redirect('login')

    cliente = get_object_or_404(Cliente, id=cliente_id)

    context = {
        'cliente': cliente
    }

    return render(request, 'admin/ver_cliente.html', context)


@login_required
def ver_certificado_cliente(request, cliente_id):

    if not _is_admin_user(request.user):
        return redirect('login')

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
        content_type, _ = mimetypes.guess_type(nombre_archivo)
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
                return _build_inline_file_response(
                    file_bytes,
                    remote_content_type,
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
                return _build_inline_file_response(file_bytes, remote_content_type, nombre_archivo)
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

    if request.method == 'POST':

        username = request.POST.get('username').lower()
        password = request.POST.get('password')
        
        # Detectar si es una petición AJAX (para cargar en modal)
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

        # Verificar si el usuario existe
        try:
            user_exists = Usuario.objects.get(username=username)
            # Usuario existe, verificar contraseña
            user = authenticate(request, username=username, password=password)
            
            if user is not None:
                login(request, user)

                if is_ajax:
                    # Retornar JSON con éxito y la URL de redirección
                    return JsonResponse({'success': True, 'redirect': _redirect_for_user(user)})
                else:
                    # Redirecciones normales
                    return redirect(_redirect_for_user(user))
            else:
                # Usuario existe pero contraseña es incorrecta
                if is_ajax:
                    return JsonResponse({'success': False, 'error': 'password_incorrect', 'message': _('Contraseña incorrecta')})
                else:
                    messages.error(request, _('Contraseña incorrecta'))
        
        except Usuario.DoesNotExist:
            # Usuario no existe en la base de datos
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'user_not_found', 'message': _('El usuario no existe en la base de datos')})
            else:
                messages.error(request, _('Usuario no existe'))

    # Detectar si es una petición AJAX (para cargar en modal)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    if is_ajax:
        return render(request, 'usuarios/login_modal_form.html')
    else:
        return render(request, 'usuarios/login.html')


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

        print("POST DATA:", request.POST)

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

        if not certificado:
            message = _("Debes adjuntar el certificado tax para completar el registro.")
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'certificado_required', 'message': message}, status=400)
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
                    ciudad=request.POST.get('ciudad'),
                    estado=request.POST.get('estado'),
                    codigo_postal=request.POST.get('codigo_postal'),
                    pais=request.POST.get('pais'),
                    sales_tax_number=request.POST.get('sales_tax'),
                    certificado_tax=certificado,
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
                messages.success(request, message)
                return redirect('login')
            else:
                # Si no se creó el usuario, mostrar error
                message = _("No se pudo completar el registro. Verifica los datos e intenta nuevamente.")
                if is_ajax:
                    return JsonResponse({'success': False, 'error': 'server_error', 'message': message}, status=400)
                messages.error(request, message)
                return redirect('registro')

        messages.success(
            request,
            _("Tu solicitud fue enviada. Un administrador revisará tu cuenta.")
        )

        if is_ajax:
            return JsonResponse({'success': True, 'message': _('Tu solicitud fue enviada. Un administrador revisará tu cuenta.')})

        return redirect('login')

    return render(request, 'usuarios/registro.html')


def login_form_modal(request):
    """Devuelve solo el formulario de login para cargar en modal"""
    from django.http import HttpResponse
    
    if request.method == 'POST':
        username = request.POST.get('username', '').lower()
        password = request.POST.get('password', '')
        
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            
            # Devolver JSON con redirect
            import json
            redirect_url = _redirect_for_user(user)
            
            return HttpResponse(
                json.dumps({'success': True, 'redirect': redirect_url}),
                content_type='application/json'
            )
        else:
            import json
            return HttpResponse(
                json.dumps({'success': False, 'error': _('Credenciales incorrectas')}),
                content_type='application/json'
            )
    
    # GET - Devolver solo el formulario
    return render(request, 'usuarios/login_modal.html')


def registro_form_modal(request):
    """Devuelve solo el formulario de registro para cargar en modal"""
    # GET - Devolver solo el formulario de registro
    return render(request, 'usuarios/registro_modal_form.html')


