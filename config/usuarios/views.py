from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from django.contrib import messages

#funcion del login
def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username').lower()
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            
            if user.groups.filter(name='Cliente').exists():
                return redirect('catalogo')
            else:
                return redirect('')

        else:
            messages.error(request, 'Credenciales incorrectas')

    return render(request, 'usuarios/login.html')


#funcion del registro
def registro_view(request):
    return render(request, 'usuarios/registro.html')

