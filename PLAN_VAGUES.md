# Plan des vagues

27 tickets en 4 vagues. Chaque vague se termine par un rapport du relecteur et par un essai manuel de Thomas.

| Vague | But | Tickets | Ce que Thomas doit fournir avant |
| --- | --- | --- | --- |
| 0 | Prouver que la chaîne de développement fonctionne | V0-01 à V0-03 (3) | Serveur du modèle et passerelle en marche |
| 1 | Poser le socle : réglages, base, entrée, agenda, brouillons, pipeline | V1-01 à V1-08 (8) | Rien pour le développement. Un export ICS de son agenda pour l'essai manuel |
| 2 | Réunions : sources, comptes rendus, actions, fiches | V2-01 à V2-09 (9) | La bibliothèque de transcription installée pour l'essai manuel |
| 3 | Imputations : captures, contrôle, relances | V3-01 à V3-07 (7) | Un échantillon anonymisé d'écran d'imputation, les règles R1 à R4 confirmées, le référentiel d'équipe |

## Vague 0, preuve de la chaîne

La vague 0 ne sert pas à livrer une fonction. Elle sert à mesurer si Qwen, piloté par Claude Code, termine trois tickets simples sans aide.

Mesures notées dans le rapport : tickets terminés sans aide, nombre d'interventions de Thomas, tickets bloqués, durée totale.

Seuil proposé pour continuer avec ce harnais : les trois tickets sont terminés et Thomas est intervenu deux fois au plus. En dessous, on change l'outil qui pilote Qwen avant d'aller plus loin (voir `docs/CHAINE.md`).

À vérifier aussi pendant cette vague, à la main : le serveur local accepte-t-il une image dans une requête ? La réponse décide du sort du ticket V3-03.

Scénario manuel : `make install`, `make check`, `make run`, ouvrir `/sante` et `/journal`, lancer `cabinet llm-test` avec le serveur du modèle allumé.

## Vague 1, socle

Ordre conseillé : V1-01, V1-02, puis V1-03, V1-04 et V1-05 dans cet ordre, puis V1-06, V1-07, V1-08.

Scénario manuel : `cabinet init` sur un répertoire d'essai, copier un fichier ICS synthétique et trois fichiers dans `entree/` (un transcript, une note, un audio), cliquer sur « Traiter maintenant », rattacher le transcript à une réunion, vérifier que l'audio refuse le rattachement sans la case de consentement, créer puis valider un brouillon à la main dans la base et vérifier le lien `mailto:`.

## Vague 2, réunions

Ordre conseillé : V2-01, V2-02, V2-03, V2-04, V2-05, V2-06, V2-07, V2-08, V2-09.

Scénario manuel, avec le modèle allumé et des données synthétiques : déposer un transcript d'une réunion fictive, traiter, lire le compte rendu, vérifier qu'un extrait inventé à la main dans la base est bien marqué « à vérifier », valider, retrouver les actions dans la page Actions, marquer la série comme suivie, vérifier la fiche de la réunion suivante.

Premier essai sur des données réelles : seulement après cette vague, par Thomas, sur sa machine, sans le relecteur. Commencer par ses propres notes avant tout enregistrement.

## Vague 3, imputations

Ordre conseillé : V3-01, V3-02, V3-04, V3-05, V3-06, V3-07, et V3-03 seulement si les images sont acceptées.

Prérequis bloquant : Thomas enregistre un écran de l'outil d'imputation (page HTML ou texte copié), remplace les noms, les codes et les chiffres par des valeurs fictives en gardant la structure, et le dépose dans `tests/fixtures/app/`. Le relecteur en tire les jeux synthétiques pendant sa phase A.

Scénario manuel : déposer une capture synthétique, relire, vérifier les anomalies contre un calcul à la main pour deux personnes, valider un brouillon de relance.

## Après la version 1

Non découpé en tickets. À cadrer avec l'architecte une fois la version 1 utilisée pendant quelques semaines.

1. Brief du jour (agenda, actions en retard, échéances, éléments à traiter).
2. Adaptateurs Microsoft Graph pour l'agenda et la messagerie, si l'accès est accordé.
3. Tri des mails et brouillons de réponse.
4. Export Word des comptes rendus au gabarit de l'entreprise.
5. Préparation de la saisie du prévisionnel d'occupation.
