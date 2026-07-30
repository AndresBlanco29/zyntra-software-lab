function nextStep(){
const page = document.getElementById("createCustomerPage");
const stepTwoText = page?.dataset.stepTwoText || "Step 2 of 2";

document.getElementById("step1").style.display="none";
document.getElementById("step2").style.display="block";

document.getElementById("progressBar").style.width="100%";
document.getElementById("stepText").innerText=stepTwoText;

}