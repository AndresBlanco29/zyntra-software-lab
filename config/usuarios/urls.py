from django.urls import path
from django.contrib.auth.views import LogoutView
from .views import login_view, registro_view, panel_admin, perfil_admin, clientes_pendientes, aprobar_cliente, ver_cliente, ver_certificado_cliente, rechazar_cliente, lista_vendedores, crear_vendedor, editar_vendedor, desactivar_vendedor, activar_vendedor, login_form_modal, registro_form_modal, verificar_username, logout_view, contenido_home, lista_testimonios, crear_testimonio, editar_testimonio, desactivar_testimonio, activar_testimonio, editar_home_contenido

urlpatterns = [
    path('login/', login_view, name='login'),
    path('login-modal/', login_form_modal, name='login_modal'),
    path('registro-modal/', registro_form_modal, name='registro_modal'),
    path('registro/', registro_view, name='registro_usuario'),
    path('verificar-username/', verificar_username, name='verificar_username'),

    path('panel-admin/', panel_admin, name='panel_admin'),
    path('panel-admin/mi-perfil/', perfil_admin, name='perfil_admin'),
    path('panel-admin/clientes-pendientes/', clientes_pendientes, name='clientes_pendientes'),
    path('panel-admin/aprobar-cliente/<int:cliente_id>/', aprobar_cliente, name='aprobar_cliente'),
    path('panel-admin/rechazar-cliente/<int:cliente_id>/', rechazar_cliente, name='rechazar_cliente'),
    path('panel-admin/ver-cliente/<int:cliente_id>/', ver_cliente, name='ver_cliente'),
    path('panel-admin/ver-certificado/<int:cliente_id>/', ver_certificado_cliente, name='ver_certificado_cliente'),

    path('panel-admin/vendedores/', lista_vendedores, name='lista_vendedores'),
    path('panel-admin/crear-vendedor/', crear_vendedor, name='crear_vendedor'),
    path('panel-admin/editar-vendedor/<int:vendedor_id>/', editar_vendedor, name='editar_vendedor'),
    path('panel-admin/desactivar-vendedor/<int:vendedor_id>/', desactivar_vendedor, name='desactivar_vendedor'),
    path('panel-admin/activar-vendedor/<int:vendedor_id>/', activar_vendedor, name='activar_vendedor'),

    path('panel-admin/contenido-home/', contenido_home, name='contenido_home'),
    path('panel-admin/contenido-home/editar/', editar_home_contenido, name='editar_home_contenido'),
    path('panel-admin/testimonios/', lista_testimonios, name='lista_testimonios'),
    path('panel-admin/testimonios/crear/', crear_testimonio, name='crear_testimonio'),
    path('panel-admin/testimonios/editar/<int:testimonio_id>/', editar_testimonio, name='editar_testimonio'),
    path('panel-admin/testimonios/desactivar/<int:testimonio_id>/', desactivar_testimonio, name='desactivar_testimonio'),
    path('panel-admin/testimonios/activar/<int:testimonio_id>/', activar_testimonio, name='activar_testimonio'),

    path('logout/', logout_view, name='logout'),
]
