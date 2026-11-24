# Décisions, hypothèses et points ouverts

Tenu par l'architecte. État au 23.11.2026.

## Décisions prises par Thomas

| Sujet | Décision |
| --- | --- |
| Priorités de la version 1 | 1. Préparation et comptes rendus de réunion. 2. Administratif récurrent. 3. Brief du jour. 4. Tri des mails |
| Autonomie des agents | Brouillons que Thomas valide. Aucun envoi |
| Sources des comptes rendus | Audio transcrit en local, transcript Teams déposé à la main, notes tapées |
| Première tâche administrative | Contrôle des imputations et relances |
| Relecture des vagues | Claude Code en local sur un modèle Claude |
| Interface | Page web locale |
| Machine | Mac Apple Silicon, 64 Go de RAM ou plus |
| Forme des imputations aujourd'hui | Lecture des écrans de l'outil, sans export |

## Choix de l'architecte, à contester si besoin

| Sujet | Choix | Raison |
| --- | --- | --- |
| Accès à l'agenda | Fichier ICS en version 1, Graph plus tard derrière la même interface | Aucune dépendance au service informatique pour démarrer |
| Pile technique | Python 3.12, FastAPI, SQLite, pytest, formulaires HTML simples | Connu de Thomas, peu de pièges pour un modèle local |
| Tests de contrat écrits par le relecteur avant chaque vague | Oui | Donne une cible précise à l'exécutant et l'empêche de se valider lui-même |
| Vérification des extraits | Contrôle déterministe de chaque citation | Seul moyen simple de repérer une décision inventée |
| Relances | Gabarit, sans modèle | Un texte de relance n'a pas besoin d'un modèle et ne doit pas varier |
| Imputations | Capture déposée puis relue à l'écran avant contrôle | Pas d'export connu, pas de pilotage de navigateur |
| Audio | L'application transcrit un fichier déposé, elle n'enregistre pas | Moins de code, et l'acte d'enregistrer reste un geste conscient |

## Hypothèses à vérifier

| Hypothèse | Qui vérifie | Quand |
| --- | --- | --- |
| Claude Code relié à Qwen par LiteLLM tient trois tickets sans aide | Thomas, relecteur | Vague 0 |
| Le serveur local accepte les images | Thomas | Vague 0 |
| L'agenda s'exporte ou se publie en ICS | Thomas | Avant l'essai manuel de la vague 1 |
| Un transcript Teams en .docx a la forme supposée | Relecteur, sur un exemple anonymisé | Phase A de la vague 2 |
| La bibliothèque de transcription tient en mémoire à côté du modèle | Thomas | Essai manuel de la vague 2 |
| Les règles R1 à R4 correspondent au contrôle que Thomas fait à la main | Thomas | Avant la vague 3 |

## Points ouverts hors du développement

1. Validation juridique de l'enregistrement et de la transcription de réunions, en interne et chez les clients. À traiter par Thomas avec les personnes compétentes avant tout usage sur une réunion réelle. L'architecte n'est pas juriste.
2. Cadre d'usage de la machine pour des données de l'entreprise et de ses clients. La version 1 traite des contenus de réunions et des imputations nominatives. Le cadre doit le permettre explicitement.
3. Demande d'accès Microsoft Graph en lecture seule auprès du service informatique, à lancer en parallèle si Thomas veut la version 2.
4. Durée de conservation des transcripts et des comptes rendus dans la base locale. Rien n'est prévu en version 1 au-delà de la suppression de l'audio.
