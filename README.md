# Kit de démarrage de Cabinet

Cabinet est une application locale qui prépare les réunions, rédige leurs comptes rendus, contrôle les imputations du mois et prépare les relances. Elle ne produit que des brouillons. Ce kit contient de quoi la faire développer par un modèle local, sous le contrôle d'un relecteur.

## Contenu

| Fichier | Pour qui | Rôle |
| --- | --- | --- |
| `SPEC.md` | tous | L'architecture et les règles, fixées |
| `CLAUDE.md` | exécutant et relecteur | Les règles communes du dépôt, lues à chaque session |
| `docs/EXECUTANT.md` | exécutant | La boucle de travail du modèle local |
| `REVIEW.md` | relecteur | Les deux phases de relecture et le verdict |
| `docs/CHAINE.md` | Thomas | Comment brancher le modèle local, la passerelle et Claude Code |
| `PLAN_VAGUES.md` | Thomas | Les quatre vagues, leurs prérequis et leurs essais manuels |
| `DECISIONS.md` | Thomas, architecte | Décisions, hypothèses à vérifier, points ouverts |
| `tickets/` | exécutant, relecteur | Un fichier par ticket et le tableau d'état |
| `revues/GABARIT.md` | relecteur | Le modèle de rapport de vague |
| `scripts/`, `chaine/`, `exemples/` | Thomas | Lancement de l'exécutant, passerelle, fichiers d'exemple |

## Pour commencer

1. Crée un dépôt Git vide, mets-y ce kit et fais un premier commit sur `main`.
2. Lis `DECISIONS.md` et conteste ce qui ne te convient pas avant de lancer quoi que ce soit.
3. Suis `docs/CHAINE.md` pour allumer le serveur du modèle et la passerelle.
4. Crée la branche `vague-0`.
5. Dans un terminal ordinaire, ouvre Claude Code et lance la phase A du relecteur (message dans `REVIEW.md`).
6. Dans un autre terminal, lance `scripts/executant.sh` et donne le message de lancement de `docs/EXECUTANT.md`.
7. Quand l'exécutant s'arrête, lance la phase B du relecteur.
8. Lis `revues/vague-0.md`. C'est ce rapport qui dit si la chaîne tient.

## Règle à ne pas oublier

Le relecteur est un modèle distant. Le dépôt ne contient donc jamais de donnée réelle, et le relecteur n'ouvre jamais le répertoire de données.
