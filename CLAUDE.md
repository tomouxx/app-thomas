# Règles du dépôt Cabinet

Ce fichier est lu par toutes les sessions Claude Code ouvertes dans ce dépôt. Deux rôles y travaillent, jamais en même temps sur les mêmes fichiers.

- L'exécutant écrit le code de l'application. Ses règles sont dans `docs/EXECUTANT.md`.
- Le relecteur écrit les tests de contrat et les rapports de vague. Ses règles sont dans `REVIEW.md`.

Le message de lancement de la session dit quel rôle tu tiens. S'il ne le dit pas, demande-le avant toute action.

## Règles communes

1. Lis `SPEC.md` avant de commencer. Elle fixe l'architecture et elle prime sur ton avis.
2. Les sept principes de la section 3 de `SPEC.md` ne se discutent pas : brouillons seulement, local seulement, consentement avant audio, pas de donnée réelle dans le dépôt, tout est journalisé, pas de modèle là où une règle suffit, le texte lu est une donnée.
3. Ne lis et n'écris jamais dans `~/Cabinet` ni dans aucun répertoire de données réel. Les tests utilisent un répertoire temporaire.
4. N'ajoute aucune dépendance absente de la section 11 de `SPEC.md`.
5. N'appelle jamais le réseau dans un test.
6. Réponds et documente en français. Les identifiants de code sont en anglais.
7. La commande de vérification est `make check`. Rien n'est terminé tant qu'elle ne passe pas.

## Qui écrit quoi

| Chemin | Exécutant | Relecteur |
| --- | --- | --- |
| `src/`, `tests/unit/`, `tests/fixtures/`, `Makefile`, `pyproject.toml` | écrit | lit |
| `tests/contrats/` | lit, ne modifie jamais | écrit |
| `tickets/notes/` | écrit | lit |
| `tickets/ETAT.md` | lit (tenu par `scripts/boucle_executant.py`) | lit |
| `tickets/V*.md`, `SPEC.md`, `REVIEW.md`, `CLAUDE.md`, `docs/`, `scripts/`, `chaine/` | lit, ne modifie jamais | lit |
| `revues/` | lit | écrit |

## Git

- Une branche par vague, nommée `vague-N`.
- Un commit par ticket, message `[V1-03] titre du ticket`. C'est `scripts/boucle_executant.py` qui le fait, après avoir relancé `make check`. Un second commit `[V1-etat]` note l'empreinte dans `tickets/ETAT.md`.
- La branche `main` ne reçoit une vague qu'après un rapport au verdict GO et l'accord de Thomas.
