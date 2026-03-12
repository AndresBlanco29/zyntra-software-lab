from django.shortcuts import render, redirect
from django.contrib import messages
from usuarios.models import Usuario
from clientes.models import Cliente
from django.contrib.auth.models import Group
import uuid


def registro_cliente(request):

    if request.method == "POST":

        # =========================
        # DATOS PERSONALES
        # =========================
        nombre = request.POST.get("nombre")
        apellido = request.POST.get("apellido")
        telefono = request.POST.get("telefono")
        id_cliente = request.POST.get("id_cliente")

        # =========================
        # DATOS EMPRESA
        # =========================
        empresa = request.POST.get("empresa")
        direccion = request.POST.get("direccion")
        direccion2 = request.POST.get("direccion2")
        ciudad = request.POST.get("ciudad")
        estado = request.POST.get("estado")
        codigo_postal = request.POST.get("codigo_postal")
        pais = request.POST.get("pais")

        sales_tax = request.POST.get("sales_tax")
        telefono_comercial = request.POST.get("telefono_comercial")
        email_comercial = request.POST.get("email_comercial")

        # =========================
        # ARCHIVO
        # =========================
        certificado = request.FILES.get("certificado")

        # =========================
        # CREAR USUARIO
        # =========================
        username = f"user_{uuid.uuid4().hex[:8]}"

        usuario = Usuario.objects.create(
            username=username,
            first_name=nombre,
            last_name=apellido,
            email=email_comercial,
            is_active=False
        )

        # =========================
        # CREAR CLIENTE
        # =========================
        Cliente.objects.create(
            usuario=usuario,
            nombre_empresa=empresa,
            telefono=telefono,
            direccion=direccion,
            ciudad=ciudad,
            estado=estado,
            sales_tax_number=sales_tax,
            certificado_tax=certificado
        )

        # asignar rol cliente
        grupo_cliente = Group.objects.get(name="Cliente")
        usuario.groups.add(grupo_cliente)

        messages.success(
            request,
            "Solicitud enviada correctamente. Nuestro equipo revisará tu documentación."
        )

        return redirect("registro")

    return render(request, "usuarios/registro.html")