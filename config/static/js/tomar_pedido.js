document.addEventListener("DOMContentLoaded", function(){

const buscador = document.getElementById("buscadorCliente");
const filas = document.querySelectorAll("tbody tr");

buscador.addEventListener("keyup", function(){

let texto = buscador.value.toLowerCase();

filas.forEach(fila => {

let nombre = fila.dataset.nombre.toLowerCase();
let empresa = fila.dataset.empresa.toLowerCase();

if(nombre.includes(texto) || empresa.includes(texto)){
fila.style.display = "";
}else{
fila.style.display = "none";
}

});

});

});