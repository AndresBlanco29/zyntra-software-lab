document.addEventListener("DOMContentLoaded", function () {

    const uploadBox = document.querySelector(".upload-box");
    const fileInput = document.getElementById("certificado");



    function nextStep() {

        let inputs = document.querySelectorAll("#step1 input");

        for (let i = 0; i < inputs.length; i++) {
            if (!inputs[i].checkValidity()) {
                inputs[i].reportValidity();
                return;
            }
        }


        document.getElementById("step1").style.display = "none";
        document.getElementById("stepCredenciales").style.display = "block";

        document.getElementById("headerRegistro").style.display = "none";
        document.getElementById("logoHeader").style.display = "none";
    }

    function nextStepCredenciales() {

        let inputs = document.querySelectorAll("#stepCredenciales input");

        for (let i = 0; i < inputs.length; i++) {

            if (!inputs[i].checkValidity()) {
                inputs[i].reportValidity();
                return;
            }

        }

        let pass1 = document.querySelector("input[name='password1']").value;
        let pass2 = document.querySelector("input[name='password2']").value;

        if (pass1 !== pass2) {
            alert("Las contraseñas no coinciden");
            return;
        }

        document.getElementById("stepCredenciales").style.display = "none";
        document.getElementById("step2").style.display = "block";

    }

window.nextStepCredenciales = nextStepCredenciales;

    // HACER ACCESIBLE LA FUNCIÓN AL HTML
    window.nextStep = nextStep;

    // SOLO ejecutar si existe uploadBox
    if (uploadBox && fileInput) {

        // PERMITIR EL ARRASTRE DE ARCHIVO A LA CAJA 
        uploadBox.addEventListener("dragover", (e) => {
            e.preventDefault();
            uploadBox.style.borderColor = "#0d6efd";
        });

        uploadBox.addEventListener("dragleave", () => {
            uploadBox.style.borderColor = "#cfd6de";
        });

        uploadBox.addEventListener("drop", (e) => {
            e.preventDefault();
            fileInput.files = e.dataTransfer.files;

            // disparar evento change
            fileInput.dispatchEvent(new Event("change"));
            uploadBox.style.borderColor = "#28a745";
        });

        //  Mostrar el nombre del archivo subido
        fileInput.addEventListener("change", function(){

            if(this.files.length > 0){
                const fileName = this.files[0].name;

                document.querySelector(".upload-content p").innerText =
                "Archivo seleccionado: " + fileName;
            }

        });
    }

});
