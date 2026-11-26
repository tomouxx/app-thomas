// Copie dans le presse-papiers de la zone visée par l'attribut data-copy
// (specs/08 §7.4). Le script ne fait rien d'autre : l'application n'envoie
// aucun message.

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) {
    return;
  }
  const area = document.getElementById(button.dataset.copy);
  if (!area) {
    return;
  }
  await navigator.clipboard.writeText(area.value);
});
