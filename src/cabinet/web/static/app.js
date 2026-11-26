// Copie dans le presse-papiers de la zone visée par l'attribut data-copy
// (specs/08 §7.4). Le script ne fait rien d'autre : l'application n'envoie
// aucun message.

document.addEventListener("click", async (event) => {
  const bouton = event.target.closest("[data-copy]");
  if (!bouton) {
    return;
  }
  const zone = document.getElementById(bouton.dataset.copy);
  if (!zone) {
    return;
  }
  await navigator.clipboard.writeText(zone.value);
});
