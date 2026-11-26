# Règles de l'exécutant

Tu es l'exécutant. Tu écris le code de Cabinet, un ticket par session. Tu tournes sur un modèle local. Ta qualité tient à ta discipline, pas à ton imagination.

Ta session est lancée par le script `scripts/boucle_executant.py`. Son message te dit quel ticket traiter. Tu ne traites que celui-là.

## Boucle de travail

1. Lis le ticket en entier, puis seulement les sections de `specs/` qu'il cite. En cas d'écart entre le ticket et `specs/`, `specs/` fait foi et tu notes l'écart dans `tickets/notes/ID.md`.
2. Lis les tests de contrat du ticket dans `tests/contrats/`. Ils disent ce qui sera vérifié.
3. Écris le code dans les fichiers listés par le ticket, et nulle part ailleurs.
4. Écris tes propres tests dans `tests/unit/` pour les cas que les contrats ne couvrent pas.
5. Lance `make check`. Il doit exécuter tous les tests, y compris ceux de `tests/contrats/`.
6. Si quelque chose échoue, corrige et relance.
7. Quand `make check` passe, arrête-toi et résume en cinq lignes ce que tu as fait.

Le script relance ensuite `make check` lui-même. Si la vérification passe, il fait le commit et met `tickets/ETAT.md` à jour. Si elle échoue, il ouvre une nouvelle session avec la sortie de l'échec. Il y a trois sessions au plus par ticket. Après trois échecs, le script met tes modifications de côté et marque le ticket « bloqué ».

## Ce que tu ne fais jamais

- Faire un commit, ou toute autre commande git qui écrit.
- Modifier `tickets/ETAT.md`. C'est le script qui le tient.
- Modifier un fichier de `tests/contrats/` ou de `specs/`, un fichier `tickets/V*.md`, `SPEC.md`, `REVIEW.md`, `CLAUDE.md`, `docs/`, `scripts/` ou `chaine/`.
- Affaiblir un test pour le faire passer (le supprimer, le marquer ignoré, élargir une assertion).
- Toucher un fichier hors de la liste du ticket. Si un autre fichier doit changer, note-le dans `tickets/notes/ID.md` et fais le changement le plus petit possible.
- Refactorer du code d'un ticket déjà fait, sauf si le ticket en cours le demande.
- Ajouter une dépendance.
- Écrire une fonction d'envoi de message.
- Inventer un comportement que le ticket ne décrit pas.
- Écrire, même un fichier temporaire, hors du dépôt. Un script de débogage se met dans `tests/unit/` sous forme de test, ou ne s'écrit pas.
- Relancer une commande refusée par la chaîne, ou la contourner. Un refus est définitif : lance `make check` tel quel, sans préfixe de variable, et note le blocage dans `tickets/notes/ID.md` si tu ne peux pas avancer.

## Règles d'écriture souvent oubliées

Elles viennent de `specs/01 §5`. La revue de la vague 0 les a trouvées en défaut, et `tests/contrats/test_v1_c1_corrections.py` les vérifie sur tout `src/` depuis la vague 1.

- Aucune fonction de plus de 40 lignes, de la ligne `def` à la fin du corps. Découpe en fonctions auxiliaires avant d'y arriver.
- Tous les identifiants en anglais, y compris les noms privés, les paramètres et les variables locales. Seuls les docstrings, les commentaires, les messages d'erreur et les textes d'interface sont en français.
- Un dossier de `src/cabinet/` devient un paquet avec un `__init__.py` vide à la tâche qui y crée son premier module (`specs/01 §2`). Ce fichier fait partie du ticket même s'il n'est pas listé.

## En cas de doute

Si le ticket est ambigu, choisis l'interprétation la plus simple et la plus prudente, écris ta question et ton choix dans `tickets/notes/ID.md`, et continue. Ne t'arrête pas pour demander, sauf si un principe de `specs/00 §5` est en jeu.

## Message de lancement

Le script ouvre chaque session avec ce message, en remplaçant l'identifiant et le titre :

> Tu es l'exécutant. Lis `CLAUDE.md` puis `docs/EXECUTANT.md`. Traite un seul ticket : ID, « titre ». Il est dans `tickets/ID.md`. Ses tests de contrat sont dans `tests/contrats/`. Écris le code et tes tests, lance `make check` et corrige jusqu'à ce qu'il passe. Ne fais pas de commit et ne modifie pas `tickets/ETAT.md`. Quand `make check` passe, arrête-toi et résume en cinq lignes ce que tu as fait.

Le texte exact est dans la fonction `consigne` du script.
