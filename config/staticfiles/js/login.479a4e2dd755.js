
      //FUNCION MOSTRAR O NO, CONTRASEÑA
      function togglePassword() {
        const passwordInput = document.getElementById("password");
        const icon = document.getElementById("toggleIcon");

        if (passwordInput.type === "password") {
          passwordInput.type = "text";

          // Mostrar contraseña → icono debe ser ojo normal
          icon.classList.remove("bi-eye-slash");
          icon.classList.add("bi-eye");
        } else {
          passwordInput.type = "password";

          // Ocultar contraseña → icono debe ser ojo tachado
          icon.classList.remove("bi-eye");
          icon.classList.add("bi-eye-slash");
        }
      }

      //FUNCION BORRAR MENSAJE ERROR DE CREDENCIALES
      setTimeout(function () {
        var mensaje = document.getElementById("mensaje-error");
        if (mensaje) {
          mensaje.style.display = "none";
        }
      }, 3000); // 3000 milisegundos = 3 segundos
    
