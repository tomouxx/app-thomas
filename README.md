# Kit de démarrage de Cabinet

Cabinet est une application locale qui prépare les réunions, rédige leurs comptes rendus, contrôle les imputations du mois et prépare les relances. Elle ne produit que des brouillons. Ce kit contient de quoi la faire développer par un modèle local, sous le contrôle d'un relecteur.

## Contenu

| Fichier | Pour qui | Rôle |
| --- | --- | --- |
| `specs/` | tous | La spécification : vision et décisions, architecture, données, thèmes, tests, plan d'implémentation |
| `SPEC.md`, `DECISIONS.md`, `PLAN_VAGUES.md` | tous | Tables de correspondance vers `specs/`, pour les renvois d'avant le 24.11.2026 |
| `CLAUDE.md` | exécutant et relecteur | Les règles communes du dépôt, lues à chaque session |
| `docs/EXECUTANT.md` | exécutant | La boucle de travail du modèle local |
| `REVIEW.md` | relecteur | Les deux phases de relecture et le verdict |
| `docs/CHAINE.md` | Thomas | Comment brancher le modèle local, la passerelle et Claude Code |
| `docs/REPRISE.md` | qui reprend le projet | Ce qui est installé, comment lancer, surveiller, arrêter et reprendre la chaîne |
| `tickets/` | exécutant, relecteur | Un fichier par ticket et le tableau d'état |
| `revues/GABARIT.md` | relecteur | Le modèle de rapport de vague |
| `scripts/boucle_executant.py` | Thomas, relecteur | Lance une session de l'exécutant par ticket, relance `make check`, fait le commit |
| `scripts/suivi.py` | Thomas | Page de suivi sur `http://127.0.0.1:8770/` |
| `scripts/chaine.sh` | Thomas | Allume et arrête le serveur du modèle et la passerelle |
| `scripts/verifier_garde.sh`, `scripts/executant.sh` | relecteur, Thomas | Contrôle des fichiers protégés, session interactive de diagnostic |
| `chaine/`, `exemples/` | Thomas | Configuration de la passerelle, fichiers d'exemple |

## Pour commencer

1. Crée un dépôt Git vide, mets-y ce kit et fais un premier commit sur `main`.
2. Lis `specs/00-vision-et-decisions.md` et conteste ce qui ne te convient pas avant de lancer quoi que ce soit.
3. Suis `docs/CHAINE.md` pour installer le modèle et la passerelle, puis lance `scripts/chaine.sh demarrer`.
4. Crée la branche `vague-0`.
5. Dans un terminal ordinaire, ouvre Claude Code et lance la phase A du relecteur (message dans `REVIEW.md`).
6. Lance `python3 scripts/suivi.py` et ouvre `http://127.0.0.1:8770/`.
7. Lance `python3 scripts/boucle_executant.py 0`. Le script ouvre une session de l'exécutant par ticket.
8. Quand la boucle s'arrête, lance la phase B du relecteur.
9. Lis `revues/vague-0.md`. C'est ce rapport qui dit si la chaîne tient.

`docs/REPRISE.md` détaille le lancement, l'arrêt et la reprise.

## Règle à ne pas oublier

Le relecteur est un modèle distant. Le dépôt ne contient donc jamais de donnée réelle, et le relecteur n'ouvre jamais le répertoire de données.
