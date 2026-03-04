from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from django.contrib import messages

def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('catalogo')  # Cambiar por la ruta real
        else:
            messages.error(request, 'Credenciales incorrectas')

    return render(request, 'usuarios/login.html')