# État des tickets

Tenu par l'exécutant. États possibles : à faire, fait, bloqué. Les tickets se traitent dans l'ordre du tableau, vague par vague.

| Ticket | Titre | Dépend de | État | Tentatives | Commit |
| --- | --- | --- | --- | --- | --- |
| V0-01 | Squelette du dépôt | aucun | à faire | 0 | |
| V0-02 | Client du modèle local et garde de bouclage | V0-01 | à faire | 0 | |
| V0-03 | Journal des exécutions d'agent | V0-01 | à faire | 0 | |
| V1-01 | Configuration et répertoire de données | V0-02, V0-03 | à faire | 0 | |
| V1-02 | Modèle de données | V1-01 | à faire | 0 | |
| V1-03 | Dossier d'entrée et agent d'ingestion | V1-02 | à faire | 0 | |
| V1-04 | Agenda par fichier ICS | V1-02 | à faire | 0 | |
| V1-05 | Gabarit web et tableau de bord | V1-02 | à faire | 0 | |
| V1-06 | Page Entrée, rattachement et consentement | V1-03, V1-04, V1-05 | à faire | 0 | |
| V1-07 | Brouillons sans envoi | V1-05 | à faire | 0 | |
| V1-08 | Orchestrateur du pipeline | V1-03, V1-04, V1-05 | à faire | 0 | |
| V2-01 | Lecture des transcripts Teams | V1-06, V1-08 | à faire | 0 | |
| V2-02 | Lecture des notes | V2-01 | à faire | 0 | |
| V2-03 | Transcription audio locale | V2-01 | à faire | 0 | |
| V2-04 | Agent de compte rendu, extraction structurée | V2-01 | à faire | 0 | |
| V2-05 | Vérification des extraits | V2-04 | à faire | 0 | |
| V2-06 | Page réunion, rendu et validation du compte rendu | V2-05, V1-07, V2-03 | à faire | 0 | |
| V2-07 | Actions et décisions | V2-06 | à faire | 0 | |
| V2-08 | Fiche de préparation | V2-07 | à faire | 0 | |
| V2-09 | Sources longues | V2-06 | à faire | 0 | |
| V3-01 | Référentiel d'équipe et jours attendus | V1-01 | à faire | 0 | |
| V3-02 | Lecture d'une capture d'imputations en texte ou HTML | V3-01, V1-08 | à faire | 0 | |
| V3-03 | Lecture d'une capture d'écran par le modèle | V3-02 | à faire | 0 | |
| V3-04 | Écran de vérification d'une capture | V3-02 | à faire | 0 | |
| V3-05 | Moteur de règles | V3-04 | à faire | 0 | |
| V3-06 | Brouillons de relance | V3-05, V1-07 | à faire | 0 | |
| V3-07 | Page imputations et échéance | V3-06 | à faire | 0 | |
