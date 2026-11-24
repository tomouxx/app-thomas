# Questions de la phase A, vague 1

Relevées le 24.11.2026 en écrivant les tests de contrat de V1-01 à V1-08. Aucune ne bloque la vague. Chaque point dit ce que les tests font en attendant une réponse.

| # | Ticket | Question | Section | Ce que font les tests |
| --- | --- | --- | --- | --- |
| Q1 | V1-04 | Un évènement renommé dont la clé de série change crée une nouvelle série. La mise à jour d'une réunion existante ne touche que son titre et sa date, pas `series_id`. La réunion reste-t-elle dans l'ancienne série, qui devient orpheline pour les occurrences suivantes ? | `specs/05 §6.2`, étape 4 | Le jeu `agenda_apres.ics` renomme par un titre de même clé (`TR: Point hebdo 12`). Le cas d'une clé qui change n'est pas testé. |
| Q2 | V1-03 | Le tableau des natures ne dit pas quoi faire d'un fichier que l'agent ne peut pas lire au moment de calculer l'empreinte (droits retirés, par exemple). | `specs/05 §5.1` | Aucun test. Le fichier verrouillé de Word (`~$…`) est couvert. |
| Q3 | V1-08 | Le code de sortie 1 de `cabinet run` « avec au moins une erreur d'élément » ne peut pas être produit avec les seuls agents d'ingestion et d'agenda. | `specs/03 §5.3`, étape 4 | Le code 1 est testé par un agent en exception (agenda absent). |
| Q4 | V1-05 | Le ticket compte toutes les actions `ouvert`, `specs/08 §2.1` ne compte que celles d'un compte rendu validé à partir de V2-07. | `specs/08 §2.1` | Le jeu place toutes les actions ouvertes sur un compte rendu validé et courant. Le test reste juste avant et après V2-07. |

Proposition du relecteur pour Q1 et Q2, à confirmer par l'architecte avant le lot 2 :

- Q1 : une réunion garde sa série. Le renommage ne touche que les réunions à venir créées après lui. La spec le dirait en une phrase dans `specs/05 §6.2`.
- Q2 : l'élément est créé à l'état `erreur`, avec un message qui donne le nom du fichier, comme pour un fichier illisible au rattachement. Un test de contrat suivrait.

Interprétations notées dans les tests, sans question ouverte : `mailto:` lu sans tenir compte de la casse, préfixe `app_` sans tenir compte de la casse, occurrence annulée rendue avec `cancelled` à vrai, case de consentement jamais cochée d'avance, garde d'origine appliquée à la lettre (`https://127.0.0.1:8765` et `null` refusés).
