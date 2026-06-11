from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from .forms import CustomerPasswordResetForm, CustomerSetPasswordForm
from .views import login_view, registro_view, panel_admin, perfil_admin, clientes_pendientes, aprobar_cliente, actualizar_precio_cliente, ver_cliente, ver_certificado_cliente, rechazar_cliente, lista_vendedores, crear_vendedor, editar_vendedor, desactivar_vendedor, activar_vendedor, login_form_modal, password_reset_form_modal, password_reset_confirm_modal, registro_form_modal, verificar_username, logout_view, contenido_home, lista_testimonios, crear_testimonio, editar_testimonio, desactivar_testimonio, activar_testimonio, editar_home_contenido, lista_usuarios_internos, crear_usuario_interno, editar_usuario_interno, desactivar_usuario_interno, activar_usuario_interno, crear_backoffice, editar_backoffice, desactivar_backoffice, activar_backoffice, corregir_solicitud_cliente, lista_asignacion_clientes_vendedores, asignar_clientes_vendedor

urlpatterns = [
    path('login/', login_view, name='login'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            form_class=CustomerPasswordResetForm,
            template_name='usuarios/password_reset_form.html',
            email_template_name='emails/password_reset_email.txt',
            html_email_template_name='emails/password_reset_email.html',
            subject_template_name='emails/password_reset_subject.txt',
            success_url=reverse_lazy('password_reset_done'),
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='usuarios/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            form_class=CustomerSetPasswordForm,
            template_name='usuarios/password_reset_confirm.html',
            success_url=reverse_lazy('password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path(
        'reset/done/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='usuarios/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
    path('login-modal/', login_form_modal, name='login_modal'),
    path('password-reset/modal/', password_reset_form_modal, name='password_reset_modal'),
    path('password-reset/confirm-modal/<uidb64>/<token>/', password_reset_confirm_modal, name='password_reset_confirm_modal'),
    path('registro-modal/', registro_form_modal, name='registro_modal'),
    path('registro/', registro_view, name='registro_usuario'),
    path('registro/correccion/<uuid:correction_token>/', corregir_solicitud_cliente, name='corregir_solicitud_cliente'),
    path('verificar-username/', verificar_username, name='verificar_username'),

    path('panel-admin/', panel_admin, name='panel_admin'),
    path('panel-admin/mi-perfil/', perfil_admin, name='perfil_admin'),
    path('panel-admin/clientes-pendientes/', clientes_pendientes, name='clientes_pendientes'),
    path('panel-admin/aprobar-cliente/<int:cliente_id>/', aprobar_cliente, name='aprobar_cliente'),
    path('panel-admin/actualizar-precio-cliente/<int:cliente_id>/', actualizar_precio_cliente, name='actualizar_precio_cliente'),
    path('panel-admin/rechazar-cliente/<int:cliente_id>/', rechazar_cliente, name='rechazar_cliente'),
    path('panel-admin/ver-cliente/<int:cliente_id>/', ver_cliente, name='ver_cliente'),
    path('panel-admin/ver-certificado/<int:cliente_id>/', ver_certificado_cliente, name='ver_certificado_cliente'),

    path('panel-admin/usuarios-internos/', lista_usuarios_internos, name='lista_usuarios_internos'),
    path('panel-admin/usuarios-internos/crear/', crear_usuario_interno, name='crear_usuario_interno'),
    path('panel-admin/usuarios-internos/editar/<int:usuario_id>/', editar_usuario_interno, name='editar_usuario_interno'),
    path('panel-admin/usuarios-internos/desactivar/<int:usuario_id>/', desactivar_usuario_interno, name='desactivar_usuario_interno'),
    path('panel-admin/usuarios-internos/activar/<int:usuario_id>/', activar_usuario_interno, name='activar_usuario_interno'),

    path('panel-admin/asignacion-clientes/', lista_asignacion_clientes_vendedores, name='lista_asignacion_clientes_vendedores'),
    path('panel-admin/asignacion-clientes/vendedor/<int:vendedor_id>/', asignar_clientes_vendedor, name='asignar_clientes_vendedor'),

    path('panel-admin/vendedores/', lista_vendedores, name='lista_vendedores'),
    path('panel-admin/crear-vendedor/', crear_vendedor, name='crear_vendedor'),
    path('panel-admin/editar-vendedor/<int:vendedor_id>/', editar_vendedor, name='editar_vendedor'),
    path('panel-admin/desactivar-vendedor/<int:vendedor_id>/', desactivar_vendedor, name='desactivar_vendedor'),
    path('panel-admin/activar-vendedor/<int:vendedor_id>/', activar_vendedor, name='activar_vendedor'),
    path('panel-admin/crear-backoffice/', crear_backoffice, name='crear_backoffice'),
    path('panel-admin/editar-backoffice/<int:usuario_id>/', editar_backoffice, name='editar_backoffice'),
    path('panel-admin/desactivar-backoffice/<int:usuario_id>/', desactivar_backoffice, name='desactivar_backoffice'),
    path('panel-admin/activar-backoffice/<int:usuario_id>/', activar_backoffice, name='activar_backoffice'),

    path('panel-admin/contenido-home/', contenido_home, name='contenido_home'),
    path('panel-admin/contenido-home/editar/', editar_home_contenido, name='editar_home_contenido'),
    path('panel-admin/testimonios/', lista_testimonios, name='lista_testimonios'),
    path('panel-admin/testimonios/crear/', crear_testimonio, name='crear_testimonio'),
    path('panel-admin/testimonios/editar/<int:testimonio_id>/', editar_testimonio, name='editar_testimonio'),
    path('panel-admin/testimonios/desactivar/<int:testimonio_id>/', desactivar_testimonio, name='desactivar_testimonio'),
    path('panel-admin/testimonios/activar/<int:testimonio_id>/', activar_testimonio, name='activar_testimonio'),

    path('logout/', logout_view, name='logout'),
]
