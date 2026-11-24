# Rapport de vague 0

Revue du 24.11.2026. Relecteur de la phase B : Claude Fable 5.2. Relecteurs automatiques par ticket : cinq agents Claude Sonnet 5.5 et un vérificateur Claude Opus 5.5 (`scripts/relecteurs.py`). Dernier commit de ticket relu : `8eb3433`. Tête de la branche `vague-0` au moment du rapport : `f2d46d2` (commits de chaîne seulement après `8eb3433`).

## Verdict

GO avec réserves. Les trois tickets tiennent tous leurs critères d'acceptation, la garde est intacte et la recette passe. Deux règles de `specs/01 §5` ne sont pas respectées (longueur d'une fonction, langue des identifiants). Elles ne sont pas des critères d'acceptation, mais elles sont à corriger avant que le code grossisse.

La vague 0 répondait à une question : ce harnais suffit-il pour piloter Qwen ? Le seuil de `specs/09 §6.1` est atteint (trois tâches terminées, zéro intervention de Thomas pour deux admises). On peut continuer avec ce harnais.

La fusion dans `main` attend l'accord de Thomas.

## Garde des fichiers protégés

```text
$ scripts/verifier_garde.sh 0
OK : aucun fichier protégé modifié depuis vague-0-tests
```

Le journal de la boucle ne compte aucune restauration de fichier protégé pendant les trois sessions.

## Vérification automatique

Jouée sur un arbre de travail neuf du commit `8eb3433`, hors du dépôt, avec un `CABINET_HOME` temporaire.

| Commande | Résultat |
| --- | --- |
| `make install` | code 0, environnement `.venv` créé |
| `CABINET_CONTRATS=tous make check` | ruff : « All checks passed! ». pytest : 95 tests réussis, 0 échec, 1 avertissement, en 3,25 s |

Les 95 tests se partagent en 56 tests de contrat (8, 31 et 17 par ticket) et 39 tests unitaires écrits par Qwen. L'avertissement est la dépréciation de `httpx` dans le client de test de starlette, prévue par `specs/00 §4.7`. Le répertoire `~/Cabinet` n'a pas été créé.

## Tickets

| Ticket | Verdict | Critère en défaut | Fichier et ligne | Attendu |
| --- | --- | --- | --- | --- |
| V0-01 | OK | aucun | | |
| V0-02 | OK | aucun | | |
| V0-03 | OK | aucun | | |

Chaque critère a été lu dans le code, pas seulement dans le résultat des tests.

### V0-01, squelette du dépôt

| Critère | Constat |
| --- | --- |
| 1. `make install` réussit dans un environnement vierge | vérifié sur l'arbre neuf |
| 2. `make check` lance ruff puis pytest et sort avec 0 | `Makefile:8` à `10` |
| 3. `GET /sante` renvoie 200 et la version lue dans les métadonnées | `src/cabinet/web/app.py:22` à `25`, observé en recette |
| 4. `cabinet version` affiche `0.1.0` | `src/cabinet/cli.py:27` à `30`, observé en recette |
| 5. La cible `run` lie le serveur à `127.0.0.1` | `Makefile:13`, port 8765 à l'écoute sur `127.0.0.1` seulement en recette |
| 6. `.gitignore` exclut les quatre motifs | `.gitignore:1` à `4` |

`pyproject.toml` ne déclare que des paquets de `specs/00 §4.11`, avec les bornes que la spécification demande. La documentation automatique de FastAPI est coupée (`/docs` répond 404).

### V0-02, client du modèle local et garde de bouclage

| Critère | Constat |
| --- | --- |
| 1. Adresses de bouclage acceptées, adresse distante refusée | `assert_loopback`, `src/cabinet/llm/client.py:22` à `32`. La comparaison porte sur l'hôte entier |
| 2. `CABINET_ALLOW_REMOTE_LLM=1` lève la garde | ligne 24, valeur exacte `1` |
| 3. Sans schéma, texte et compteurs | lignes 106 à 111 et 164 à 168 |
| 4. Avec schéma, JSON validé, clôture de bloc retirée | `_sans_bloc_code` lignes 45 à 52, validation ligne 114 |
| 5. Deux réponses invalides puis une valide, trois invalides lèvent `LLMError` | boucle lignes 101 à 137. La relance ajoute deux messages au dialogue |
| 6. Image envoyée en URI de données | `_avec_images` et `_uri_de_donnees`, lignes 170 à 192 |
| 7. Code HTTP 500 lève `LLMError` | ligne 155 |
| 8. Aucun test n'ouvre de connexion | les tests de Qwen passent tous un `httpx.MockTransport` |

Le corps de la requête ne porte que `model`, `messages` et `temperature` (ligne 145). Seul `message.content` est lu. Les messages d'erreur sont ceux de `specs/04 §3`, mot pour mot, et ne reprennent jamais le texte d'un message.

### V0-03, journal des exécutions d'agent

| Critère | Constat |
| --- | --- |
| 1. Bloc sans exception : ligne `ok` avec `ended_at` | `src/cabinet/agents/journal.py:49` à `52` |
| 2. Bloc en exception : ligne `erreur`, message gardé, exception relancée | lignes 40 à 48. L'annulation précède la relecture de la ligne, comme le demande `specs/05 §2.2` |
| 3. `GET /journal` renvoie 200 et affiche les agents | `src/cabinet/web/routes/journal.py`, observé en recette |
| 4. La base est créée sous `CABINET_HOME` et nulle part ailleurs | `src/cabinet/db/base.py:50` à `54`, observé en recette |
| 5. Au plus 100 lignes affichées | `routes/journal.py:27` |

`clock.py` est le seul module qui lit l'heure du système.

### Réserves

Ces écarts ne touchent aucun critère d'acceptation. Ils viennent de ma lecture et des relecteurs automatiques.

| # | Ticket | Écart | Fichier et ligne | Règle | Attendu |
| --- | --- | --- | --- | --- | --- |
| R1 | V0-02 | `LocalLLM.complete` fait 55 lignes et se termine par une instruction inatteignable | `src/cabinet/llm/client.py:86` à `140` | `specs/01 §5`, ligne « Longueur » | fonction de 40 lignes au plus, découpée |
| R2 | tous | Les noms privés et les variables locales sont en français (`_appeler`, `_chemin_base`, `routeur`, `sante`, `execution`) | tout `src/` | `specs/01 §5`, ligne « Langue du code », et `CLAUDE.md` règle 6 | identifiants en anglais. Les noms publics du contrat le sont déjà |
| R3 | V0-03 | La route `/journal` annonce `-> str` et renvoie une réponse de gabarit | `src/cabinet/web/routes/journal.py:20` | annotation exacte | type de retour juste, ou pas d'annotation |
| R4 | V0-03 | Les tests de Qwen ne couvrent ni l'annulation du travail en attente ni l'interruption | `tests/unit/test_journal.py` | `specs/05 §2.3`, cas attendus | deux tests de plus. Les tests de contrat couvrent déjà la ligne en erreur |
| R5 | V0-03 | Le chemin `~/Cabinet` est écrit dans `db/base.py` | `src/cabinet/db/base.py:53` | `specs/09 §5`, contrôle en vigueur depuis V1-01 | à déplacer dans `config.py` par V1-01, comme prévu |

## Principes de `specs/00 §5`

| Principe | Tenu | Preuve |
| --- | --- | --- |
| 1. Brouillons seulement | oui | `grep -rn "smtplib\|sendMail\|send_message" src/` ne renvoie rien |
| 2. Local seulement | oui | garde de bouclage appelée par le constructeur de `LocalLLM`, serveur lié à `127.0.0.1`, aucun `http://` ni `https://` dans les gabarits |
| 3. Consentement avant audio | sans objet | aucun code audio avant V1-06 et V2-03 |
| 4. Pas de donnée réelle dans le dépôt | oui | les seuls domaines de `tests/` sont `exemple.test`, `localhost` et des adresses de bouclage ou privées |
| 5. Tout est journalisé | oui | `record_run` suit l'algorithme de `specs/05 §2.2`. Aucun agent n'existe encore |
| 6. Pas de modèle là où une règle suffit | sans objet | aucun agent |
| 7. Le texte lu est une donnée | sans objet | aucun prompt de tâche. Les messages d'erreur du client ne citent pas la réponse du modèle |

L'arborescence de `src/` ne contient que des fichiers prévus par `specs/01 §2`.

## Recette (`specs/09 §6.1`)

Jouée sur l'arbre neuf, avec un `CABINET_HOME` temporaire.

| Étape | Observé |
| --- | --- |
| `make install` sur un clone neuf | code 0 |
| `CABINET_CONTRATS=tous make check` | ruff ne signale rien, 95 tests passent |
| `make run`, puis `/sante` | `{"statut":"ok","version":"0.1.0"}`, code 200 |
| `/journal` | la page s'affiche avec « Aucune exécution enregistrée. » |
| `cabinet llm-test`, serveur du modèle allumé | `OK`, code 0, en 5 secondes |
| `cabinet llm-test`, serveur éteint | « Le serveur du modèle est injoignable à http://127.0.0.1:8080/v1. », code 1 |

Pour l'étape « serveur éteint », j'ai arrêté le serveur de LM Studio puis je l'ai relancé sur le port 8080. Le modèle est resté chargé et la sonde répond de nouveau `OK`.

Thomas doit encore jouer cette recette de son côté.

## Notes de l'exécutant

Toutes les interprétations sont prudentes et documentées. Je les accepte.

| Ticket | Interprétation | Avis |
| --- | --- | --- |
| V0-01, V0-02, V0-03 | Fichiers `__init__.py` vides créés hors de la liste du ticket | acceptée, `specs/01 §2` les exige |
| V0-01 | Option `audio` absente de `pyproject.toml` | acceptée, `specs/00 §4.12` la renvoie à V2-03 |
| V0-03 | `tests/unit/conftest.py` ajouté pour isoler `CABINET_HOME` | acceptée. Sans lui, `test_sante.py` aurait créé `~/Cabinet` |
| V0-03 | `functools.cache` à la place de `lru_cache` | acceptée, même mécanisme, imposé par la règle ruff UP |

Aucune interprétation refusée.

## Conduite de l'exécutant

Ce que les sessions montrent, au-delà du code livré.

- Qwen n'a tenté aucun commit et n'a touché aucun fichier protégé.
- Dix commandes ont été refusées par la liste des permissions (2, 2 et 6 par session). Ce sont surtout des commandes préfixées par une variable d'environnement, des `printenv` et des redirections. Après un refus, Qwen relance parfois la même commande, puis contourne.
- Pendant V0-03, après le refus d'un script écrit par redirection, Qwen a écrit `/tmp/debug_tri.py` avec l'outil d'écriture, l'a exécuté, puis n'a pas pu le supprimer. Le fichier est un script de débogage sans donnée réelle. Il est toujours dans `/tmp`. C'était un trou de la chaîne, pas une faute contre un ticket. Il est bouché (section suivante).
- Pendant V0-03, le résumé automatique du contexte a pris 19 minutes et 30 secondes, soit un cinquième de la session.

## Ce que la revue a changé dans la chaîne

Aucun réglage de la boucle n'a eu lieu pendant les sessions. Les commits `[V0-chaine]` faits pendant la vague n'ont touché que la page de suivi et les relecteurs. Après la fin de la boucle :

- La boucle refuse à l'exécutant toute écriture dans `/tmp`, `/private/tmp`, `/var/folders` et `~/Cabinet`, et toute lecture de `~/Cabinet`. Vérifié par un essai : l'écriture d'un fichier dans `/tmp` est refusée.
- Un angle de relecture en erreur est rejoué une fois sur un autre modèle. S'il échoue encore, le ticket ne peut plus sortir « ok ». La première relecture de V0-03 était sortie « ok » alors que l'angle Conduite était tombé en erreur. Elle a été rejouée, complète cette fois.
- La boucle garde un rapport de test par vérification, diffuse la pensée de l'exécutant mot à mot et sait s'arrêter entre deux tickets.

## Pour l'architecte

Ce que l'exécutant fait bien :

- Il suit la spécification de près, jusqu'aux messages d'erreur recopiés mot pour mot.
- Il note chaque écart dans `tickets/notes/`, avec la section de spécification qui le justifie.
- Il a terminé les trois tickets du premier coup.
- Il protège de lui-même le répertoire de données réel (isolation de `CABINET_HOME` dans ses tests).

Ce qu'il rate de façon répétée :

- Les identifiants en anglais. Il respecte les noms du contrat et écrit tout le reste en français.
- La limite de 40 lignes par fonction, dès que l'algorithme de la spécification est long.
- Les refus de permission. Il insiste ou contourne au lieu de s'arrêter.
- Le temps. La moitié d'une session passe en raisonnement, un bon tiers à relire le contexte. V0-03 a duré 1 h 38.

Ce qu'il faudrait changer dans la forme des tickets :

- Lister les fichiers `__init__.py` et `conftest.py` attendus. Les trois tickets ont produit un constat « fichier hors liste » pour un fichier que la spécification exige.
- Rappeler dans chaque ticket les deux règles de `specs/01 §5` qu'il oublie, ou les faire vérifier par un test de contrat transversal (longueur des fonctions).
- Dire à l'exécutant quelles commandes passent (`make check` sans préfixe, pas de fichier hors du dépôt).
- Garder des tickets de la taille de V0-02. V0-03 a déjà déclenché un résumé du contexte.

## Mesures

Ces mesures couvrent la boucle lancée à 17:35, après la révision de la spécification. La tentative du matin, interrompue par un redémarrage du Mac puis par cette révision, est archivée dans `.chaine/archives/` et n'est pas comptée.

| Mesure | Valeur |
| --- | --- |
| Tickets terminés sans aide | 3 sur 3 |
| Tickets bloqués | 0 |
| Interventions de Thomas | 0 (seuil : 2 au plus) |
| Réglages de la chaîne par le relecteur pendant les sessions | 0 |
| Sessions lancées | 3 |
| Durée de la vague | 3 h 05, de 17:35 à 20:41 |
| Fichiers protégés restaurés par la garde | 0 |
| Débit de Qwen en raisonnement | 14 tokens par seconde en moyenne |
| Coût des relecteurs automatiques | 3,76 $ pour quatre relectures (2,72 $ pour les trois rapports en vigueur) |

| Ticket | Tentatives | Durée | Tours | Erreurs d'outil | Refus | Contrats réussis | Fichiers hors liste | Relecture automatique |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| V0-01 | 1 | 29 min | 30 | 2 | 2 | 8 | 1 | ok (1 mineur, 3 remarques) |
| V0-02 | 1 | 57 min | 29 | 3 | 2 | 31 | 2 | réserves (1 majeur, 6 remarques) |
| V0-03 | 1 | 1 h 38 | 66 | 8 | 6 | 17 | 4 | ok à la seconde relecture (2 mineurs, 8 remarques) |

Projection, à prendre comme une estimation : à ce rythme, les 24 tickets restants demandent environ 25 heures de calcul et 23 $ de relecture. Les tickets des lots suivants sont plus gros que ceux du lot 0.

## Suite proposée

1. Thomas joue la recette et donne ou non son accord pour la fusion dans `main`.
2. Les réserves R1 à R4 deviennent un ticket de correction, traité en tête de la vague 1. Thomas a marqué « à corriger » les constats confirmés de V0-01 et V0-02 dans la page de suivi. Les constats de la seconde relecture de V0-03 attendent encore son tri.
3. Phase A de la vague 1 : tests de contrat, plus un test transversal sur la longueur des fonctions.
