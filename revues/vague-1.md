# Rapport de vague 1

Revue du 26.11.2026. Relecteur de la phase B : Claude Opus 5.5, aidé de trois agents de lecture (Claude Opus 5.5) qui ont relu chacun les critères de trois tickets dans le code. Relecteurs automatiques par ticket : cinq agents Claude Sonnet 5.5 et un vérificateur Claude Opus 5.5 (`scripts/relecteurs.py`). Dernier commit de ticket relu : `ecea27d` (V1-08). Après lui, la branche `vague-1` ne porte que des commits `[V1-etat]`, `[V1-chaine]` et `[V1-tests]`.

## Verdict

GO avec réserves. Les neuf tickets tiennent tous leurs critères d'acceptation, la garde est intacte, `make check` passe sur un arbre neuf et la recette du lot 1 passe sur ses six étapes.

Les réserves ne touchent aucun critère d'acceptation. Deux sont nouvelles et méritent une correction avant le lot 2 : deux routes écrites en `async def` contre `specs/01 §3.5`, et des commandes refusées que Qwen relance sous une autre forme. Les autres reprennent des défauts déjà vus en vague 0, en particulier les identifiants en français, cette fois dans les tests.

La fusion dans `main` attend l'accord de Thomas.

## Garde des fichiers protégés

```text
$ scripts/verifier_garde.sh 1
OK : aucun commit de ticket ne modifie un fichier protégé depuis vague-1-tests
```

La première exécution répondait NO-GO à cause de `docs/REPRISE.md`, que j'avais modifié moi-même dans le commit `[V1-chaine]` `b32970d`. Le script regardait toute la branche, y compris les réglages de la chaîne. Il ne regarde plus que les commits de ticket et protège aussi `scripts/` et `chaine/` (commit `26ebc36`). Un essai sur un arbre jetable, avec un faux commit `[V1-09]` qui modifie `specs/`, donne bien NO-GO. J'ai aussi relu à la main la liste des fichiers des 23 commits de la vague : aucun commit de Qwen ne touche `tests/contrats/`, `specs/`, `docs/`, `scripts/`, `chaine/` ni un ticket.

## Vérification automatique

Jouée sur un arbre de travail neuf du commit `ecea27d`, hors du dépôt, avec un `CABINET_HOME` temporaire.

| Commande | Résultat |
| --- | --- |
| `make install` | code 0 |
| `CABINET_CONTRATS=tous make check` | ruff : « All checks passed! ». pytest : 645 tests réussis, 0 échec, 2 avertissements, en 9 s |

Les 645 tests se partagent en 475 tests de contrat (56 de la vague 0, 419 de la vague 1) et 170 tests unitaires écrits par Qwen. Par ticket de la vague 1 : V1-C1 8, V1-01 41, V1-02 68, V1-03 99, V1-04 34, V1-05 38, V1-06 49, V1-07 52, V1-08 30.

Le premier avertissement est la dépréciation de `httpx` dans le client de test, déjà vue en vague 0. Le second était une faute de ma part : dans le contrat V1-C1, la fonction auxiliaire `tests_de_record_run` commençait par `test` et pytest la collectait comme un test qui passait sans rien vérifier. Elle est renommée (commit `d0b5d08`), le contrat compte maintenant 7 tests. Le répertoire `~/Cabinet` n'a pas été créé.

Le message du commit de phase A `edaefaf` annonçait 452 tests de contrat. pytest en collecte 419. Les fichiers de contrat n'ont pas changé entre `edaefaf` et `ecea27d`, aucun test n'a donc été perdu. Je n'ai pas retrouvé l'origine de l'écart, qui vient de mon décompte au moment du commit.

## Tickets

| Ticket | Verdict | Critère en défaut | Fichier et ligne | Attendu |
| --- | --- | --- | --- | --- |
| V1-C1 | OK | aucun | | |
| V1-01 | OK | aucun | | |
| V1-02 | OK | aucun | | |
| V1-03 | OK | aucun | | |
| V1-04 | OK | aucun | | |
| V1-05 | OK | aucun | | |
| V1-06 | OK | aucun | | |
| V1-07 | OK | aucun | | |
| V1-08 | OK | aucun | | |

Chaque critère a été lu dans le code, pas seulement dans le résultat des tests. Les tableaux suivants donnent la preuve, au commit `ecea27d`.

### V1-C1, corrections de la vague 0

| Critère | Constat |
| --- | --- |
| 1. Aucune fonction de `src/` au-delà de 40 lignes | mesure par l'arbre syntaxique : la plus longue fait 35 lignes. `complete` fait 29 lignes (`src/cabinet/llm/client.py:86` à `114`), découpée en `_initial_messages`, `_result` et `_retry`. Le `raise` inatteignable est retiré, les messages d'erreur sont inchangés |
| 2. Aucun des 45 noms français de la précision 2 | aucun n'est encore défini dans `src/`, par exemple `_LOCAL_HOSTS`, `_db_path`, `router`, `health` |
| 3. `GET /journal` n'annonce plus `-> str` | `src/cabinet/web/routes/journal.py:20` |
| 4. Deux tests de `record_run` | interruption et annulation, `tests/unit/test_journal.py:79` à `95` |
| 5. Contrats V0-01 à V0-03 toujours verts | vérifié |

Les réserves R1 à R4 de la vague 0 sont levées. R5 (`~/Cabinet` hors de `config.py`) est levée par V1-01.

### V1-01, configuration et répertoire de données

| Critère | Constat |
| --- | --- |
| 1. `cabinet init` crée cinq dossiers et `reglages.toml` | `src/cabinet/cli.py:36` à `54`, observé en recette. Le fichier écrit est identique à `exemples/reglages.exemple.toml` |
| 2. Un second `init` garde un `reglages.toml` modifié | écriture seulement si le fichier manque, `cli.py:51` |
| 3. Valeurs par défaut de `specs/03 §3` | `src/cabinet/config.py:103` à `155`, champ par champ |
| 4. Une clé inconnue lève `ConfigError` qui la nomme | `extra="forbid"`, `config.py:100` et `184` à `188` |
| 5. Une adresse distante lève `ConfigError` | `assert_loopback` dans `load_settings`, `config.py:161` |
| 6. `~/Cabinet` seulement dans `config.py` | seule occurrence `config.py:94` |

### V1-02, modèle de données

| Critère | Constat |
| --- | --- |
| 1. Quatorze tables aux noms exacts | `src/cabinet/db/models.py:127` à `397`, confirmé par l'inspection d'une base temporaire |
| 2. Un `transcript` sans réunion échoue | clé étrangère non nulle et `PRAGMA foreign_keys=ON` à chaque connexion (`db/base.py:71` à `74`) |
| 3. Unicité de `sha256` et de (`uid`, `start`) | `models.py:156` et `177`, avec les autres unicités de `specs/02 §2` |
| 4. Supprimer une réunion supprime ses enfants | `ON DELETE CASCADE` en SQL et `cascade="all, delete-orphan"` par l'ORM. `inbox_item` et `draft` passent à nul |
| 5. Aller-retour de chaque modèle | contrat paramétré sur les 14 tables |
| 6. `init_db()` deux fois sans perte | `db/base.py:92` à `96` |

### V1-03, dossier d'entrée et agent d'ingestion

| Critère | Constat |
| --- | --- |
| 1. Tableau des natures de `specs/05 §5.1`, extension en majuscules comprise | `src/cabinet/adapters/folder_inbox.py:73` à `93` |
| 2. Date tirée du nom, `None` sinon | `folder_inbox.py:21` et `96` à `104`. Une date impossible donne `None` |
| 3. Fichier caché, `~$…` et fichier frais ignorés | `folder_inbox.py:43` à `50`, délai mesuré par `clock.now()` |
| 4. Pas de doublon après deux passages | empreintes chargées en `src/cabinet/agents/ingestion.py:32` et complétées pendant le passage |
| 5. Audio à `consentement_requis`, capture à `pret`, `.zip` à `ignore` | `ingestion.py:74` à `82` |
| 6. `processed` compte les nouveaux éléments | `ingestion.py:45` et `46` |

Le contenu d'un fichier n'est lu que pour son empreinte. Aucun modèle n'est appelé.

### V1-04, agenda par fichier ICS

| Critère | Constat |
| --- | --- |
| 1. Évènement simple lu en entier | `src/cabinet/adapters/ics_calendar.py:51` à `68`. Adresses sans `mailto:`, en minuscules, sans doublon |
| 2. Une occurrence par semaine dans la fenêtre | `recurring_ical_events` entre deux bornes en `LOCAL_TZ`, `ics_calendar.py:30` à `32` |
| 3. Annulé par statut ou par titre, sans réunion | `ics_calendar.py:120` à `124`, `src/cabinet/agents/agenda.py:47` |
| 4. Égalités de `series_key` | `src/cabinet/services/series.py:18` à `30`, les cinq lignes de `specs/05 §6.3` |
| 5. Pas de doublon d'évènement, de série ni de réunion | `agenda.py:56` à `58`, `73` et `91` |
| 6. ICS absent : erreur claire, enregistrée au journal | message de la spec en `ics_calendar.py:39` à `43`, ligne `erreur` écrite par `record_run` |
| 7. Heures dans le fuseau local | `ics_calendar.py:76` à `87`, relues en `LOCAL_TZ` par `TZDateTime` |

### V1-05, gabarit web et tableau de bord

| Critère | Constat |
| --- | --- |
| 1. `GET /` répond 200 sur une base vide | `src/cabinet/web/routes/dashboard.py:44` à `64` |
| 2. Huit liens sur `/` et `/journal` | `src/cabinet/web/templates/base.html:10` à `17`, dans l'ordre de `specs/08 §1.4` |
| 3. Les quatre compteurs reflètent la base | `dashboard.py:117` à `178`. Les comptes rendus comptés sont les versions courantes |
| 4. Échéance « J-3 », « aujourd'hui », « échéance dépassée » | `dashboard.py:181` à `190`, date tirée de `clock.today()` |
| 5. Aucun `http://` ni `https://` dans les gabarits et le CSS | vérifié par `grep` |
| 6. Réunions des sept prochains jours, dans l'ordre | `dashboard.py:96` à `114`, observé en recette |

### V1-06, page Entrée, rattachement et consentement

| Critère | Constat |
| --- | --- |
| 1. Réunions du bon jour, triées par similarité | `src/cabinet/services/matching.py:28` à `56` |
| 2. Transcript rattaché, `meeting_id` fixé, état `pret` | `src/cabinet/web/routes/inbox.py:171` à `178` et `217` à `223`, observé en recette |
| 3. Audio sans la case : 400, état inchangé | `inbox.py:160` et `161`, avant toute écriture. Observé en recette |
| 4. Audio avec la case : `pret` et `consent_at` | `inbox.py:220` à `223` |
| 5. Réunion hors agenda créée avec sa série | `inbox.py:179` à `187` et `203` à `214` |
| 6. Ignorer passe à `ignore` | `inbox.py:87` à `98` |
| 7. Élément `traite` : 409 | `inbox.py:156` et `157` |

Le consentement est contrôlé au rattachement (`inbox.py:160`). La case n'apparaît que pour un audio et n'est jamais cochée d'avance. Le second contrôle de `specs/09 §5` appartient à l'agent de transcription, qui arrive avec V2-03. Le scénario S4 ne pourra être joué qu'alors.

### V1-07, brouillons sans envoi

| Critère | Constat |
| --- | --- |
| 1. Transitions permises, 409 pour les autres | table en `src/cabinet/services/drafts.py:24` à `27`, contrôle en `76` et `77` |
| 2. Fichier d'export à la validation | `drafts.py:79` et `80`, `105` à `121`. Observé en recette |
| 3. Modifier un brouillon validé : 409 | `drafts.py:60` et `61` |
| 4. Pas de lien `mailto:` avant validation | calculé et affiché seulement à l'état `valide` |
| 5. Le lien décodé redonne l'objet et le corps | formule exacte de `specs/08 §7.3`, `drafts.py:85` à `94`. Observé en recette |
| 6. Aucune fonction d'envoi | `grep` vide, de même pour `SMTP`, `subprocess`, `webbrowser` et `osascript` |

### V1-08, orchestrateur du pipeline

| Critère | Constat |
| --- | --- |
| 1. Agents exécutés dans l'ordre | `src/cabinet/agents/pipeline.py:48` et `49` |
| 2. Agent en exception journalisé, le suivant s'exécute | `pipeline.py:57` à `66` |
| 3. Verrou tenu : `PipelineBusy` | création atomique en `pipeline.py:92` à `94`. Un verrou périmé n'est repris qu'une fois |
| 4. Verrou retiré après un passage normal ou en erreur | `finally` en `pipeline.py:51` et `52`, pris avant le `try` |
| 5. `cabinet run` sort avec 0, 1 ou 2 | `src/cabinet/cli.py:58` à `78` |
| 6. `POST /traiter` redirige avec un résumé | `dashboard.py:82` à `93` et `193` à `211`, observé en recette |

### Réserves

Ces écarts ne touchent aucun critère d'acceptation. Ils viennent des trois lectures, de la mienne et des relecteurs automatiques. Je n'ai gardé que ce qui a été vérifié dans le code ou dans une session.

| # | Ticket | Écart | Fichier et ligne | Règle | Attendu |
| --- | --- | --- | --- | --- | --- |
| R1 | V1-06, V1-07 | Deux routes en `async def` lisent le formulaire par `await request.form()`. L'accès à SQLite se fait alors dans la boucle d'évènements, et la page peut se figer pendant un passage | `web/routes/inbox.py:68`, `web/routes/drafts.py:51` | `specs/01 §3.5`, `specs/08 §1.2` | `def` synchrone avec `Form(...)`. `python-multipart` est déjà une dépendance |
| R2 | V1-02, V1-03, V1-04 | Après le refus d'une commande, Qwen obtient le même effet sous une autre forme : `CABINET_TICKET=V1-02 make check` refusé, puis pytest lancé par `python -c` avec la variable posée. `env` refusé, puis `printenv`. `python3 -c "import icalendar"` refusé, puis `.venv/bin/python -c` | sessions de V1-02, V1-03 et V1-04 | `docs/EXECUTANT.md`, « Ce que tu ne fais jamais » | un refus est définitif. La note du ticket le dit |
| R3 | V1-C1, V1-01, V1-02, V1-04 | Identifiants français dans le code neuf, surtout dans les tests (`statut`, `RACINE`, `resultat`, `_serie`, `test_cle_de_serie_*`) et encore dans `src/` pour V1-04 (`_lire_calendrier`, `_upsert_reunion`, `serie`) et `app.js` (`bouton`, `zone`) | `tests/unit/test_journal.py:79`, `tests/unit/test_config.py:18`, `tests/unit/test_models.py:45`, `adapters/ics_calendar.py:29` à `124`, `agents/agenda.py:23` à `96`, `static/app.js:6` | `specs/01 §5`, `docs/EXECUTANT.md` | identifiants en anglais partout. Le test de langue de V1-C1 ne visait que les 45 noms de la vague 0 |
| R4 | V1-03, V1-04, V1-05, V1-07 | Choix pris sans note : élément en échec annulé sans trace, journée entière sans `DTEND` qui finit le lendemain, champ absent lu comme chaîne vide, aplatissement des réglages. Aucun fichier `tickets/notes/` pour V1-03, V1-04 et V1-05 | `agents/ingestion.py:40` à `42`, `adapters/ics_calendar.py:55`, `web/routes/drafts.py:140` | `docs/EXECUTANT.md` | une note par choix |
| R5 | V1-01 | `set_cra_deadline` livrée par anticipation, sans note. Le plan la place dans V3-07 | `config.py:198` à `286` | périmètre du ticket | la garder et le noter, ou la retirer. Son comportement suit `specs/03 §3.2` |
| R6 | V1-04 | Le dossier `services/` n'a pas de `__init__.py` | `src/cabinet/services/` | `specs/01 §2` | un `__init__.py` vide |
| R7 | V1-06, V1-07 | Logique métier dans la route (réunion hors agenda, consentement, état après « Réessayer »). Champs de formulaire sans `<label>` | `web/routes/inbox.py:203` à `233`, `templates/inbox.html:30`, `templates/drafts.html:46` | `specs/08 §1.2` et `§1.4` | un service, un libellé par champ |
| R8 | V1-08 | `cabinet run` ne rattrape pas `ConfigError` et finit sur une trace Python, alors que `llm-test` le fait | `cli.py:60` | cohérence de `specs/03 §5` | message en français et code 1 |
| R9 | plusieurs | Constructeurs sans `-> None`, paramètres de test non annotés | `folder_inbox.py:27`, `ingestion.py:27`, `ics_calendar.py:24`, `agenda.py:32`, `test_models.py:49` | `specs/01 §5` | annotations |

### Écart accepté : le cache de moteurs de `db/base.py`

V1-06 a remplacé le cache par chemin (`functools.cache`) de `get_engine` par un dictionnaire qui ne garde qu'un moteur et libère le précédent (`src/cabinet/db/base.py:56` à `77`). Le fichier n'était pas dans la liste du ticket et `specs/05 §2.1` demande un `lru_cache`.

J'accepte l'écart. La première tentative de V1-06 a échoué sur `OSError: [Errno 24] Too many open files` à la vérification de la boucle : chaque test ouvre une base dans un `CABINET_HOME` neuf, le cache gardait un moteur par test, et la boucle tournait sous launchd, qui limite un processus à 256 fichiers ouverts. Qwen a trouvé la cause, a fait le plus petit changement, l'a noté dans `tickets/notes/V1-06.md` et le comportement visible ne change pas (un moteur par chemin). La limite de launchd est maintenant portée à 4096 (commit `26db180`).

Il reste à aligner `specs/05 §2.1` sur le code. Je propose de le faire en phase A de la vague 2, si Thomas est d'accord.

## Contrôles transversaux (`specs/09 §5`) et principes de `specs/00 §5`

| Contrôle | Tenu | Preuve |
| --- | --- | --- |
| Aucune fonction d'envoi | oui | `grep -rn "smtplib\|sendMail\|send_message" src/` ne renvoie rien |
| `~/Cabinet` seulement dans `config.py` | oui | seule occurrence `config.py:94` |
| Heure du système dans un seul module | oui | `datetime.now` seulement dans `clock.py` |
| Aucune ressource externe | oui | aucun `http://` ni `https://` dans `web/templates/` et `web/static/` |
| Garde de bouclage | oui | contrats de `specs/04 §2` verts, `load_settings` refuse une adresse distante |
| Garde d'origine | oui | `POST /traiter` avec une origine distante : 403 en recette |
| Consentement contrôlé deux fois | à moitié, comme prévu | contrôle au rattachement en place. Le second arrive avec V2-03 |
| Journal complet | oui | une ligne `agent_run` par agent et par passage, observé en recette |
| Dépendances | oui | `pyproject.toml` inchangé depuis la vague 0 |
| Arborescence | oui, avec R6 | tous les fichiers de `src/` sont prévus par `specs/01 §2` |
| Données réelles | oui | seuls domaines de messagerie dans `tests/` : `exemple.test` |

| Principe | Tenu | Preuve |
| --- | --- | --- |
| 1. Brouillons seulement | oui | aucune fonction d'envoi. Le lien `mailto:` n'apparaît qu'à l'état `valide` et n'envoie rien |
| 2. Local seulement | oui | garde de bouclage, garde d'origine, serveur sur `127.0.0.1` |
| 3. Consentement avant audio | oui | tout audio entre à `consentement_requis`. Le rattachement exige la case à chaque fois |
| 4. Pas de donnée réelle dans le dépôt | oui | jeux synthétiques, domaine `exemple.test` |
| 5. Tout est journalisé | oui, avec R4 | chaque agent passe par `record_run` dans l'orchestrateur. Un élément d'entrée en échec n'en garde aucune trace |
| 6. Pas de modèle là où une règle suffit | oui | ingestion, agenda et rattachement sont des règles. Aucun appel au modèle dans la vague |
| 7. Le texte lu est une donnée | sans objet | aucun prompt de tâche dans la vague |

## Recette (`specs/09 §6.2`)

Jouée sur l'arbre neuf de `ecea27d`, avec un `CABINET_HOME` temporaire, un agenda ICS synthétique de deux réunions dans la semaine et trois fichiers synthétiques.

| Étape | Observé |
| --- | --- |
| `cabinet init` | cinq dossiers et `config/reglages.toml`, code 0 |
| ICS dans `config/agenda.ics`, un transcript, une note et un audio dans `entree/`, puis « Traiter maintenant » | « Traitement terminé. ingestion : 3, agenda : 2 ». Les trois fichiers sont à la page Entrée, l'audio sous « Consentement requis ». Les deux réunions sont au tableau de bord sous « Réunions des sept prochains jours » |
| Rattacher le transcript à une réunion | redirection 303, l'élément passe à `pret` |
| Rattacher l'audio sans cocher la case | 400 et « Le consentement des participants est requis pour un enregistrement. ». L'élément reste à `consentement_requis` |
| Créer un brouillon à la main dans la base, le valider | état `valide`. Le lien `mailto:` porte les deux destinataires, l'objet et le corps encodés. Le fichier `brouillons/2026-11-26_envoi_cr_1.md` existe avec l'objet, les destinataires et le corps |
| Ouvrir `/journal` | une ligne `ok` par agent et par passage, trois passages, six lignes |

Thomas doit encore jouer cette recette de son côté, après avoir vérifié que son agenda s'exporte en ICS (`specs/00 §4.13`).

## Notes de l'exécutant

| Ticket | Interprétation | Avis |
| --- | --- | --- |
| V1-01 | Membres de `Paths` en champs plutôt qu'en propriétés | acceptée, conforme à `specs/03 §1` |
| V1-01 | `mkstemp` à la place de `NamedTemporaryFile` | acceptée, le renommage reste atomique |
| V1-01 | « Les assertions des contrats sont reproduites dans les tests unitaires » | refusée telle qu'écrite : 23 tests unitaires pour 35 fonctions de contrat. Sans conséquence |
| V1-02 | `make check` sans préfixe exécute les contrats, la boucle exporte `CABINET_TICKET` | acceptée, vérifiée |
| V1-06 | Case exigée à chaque rattachement d'un audio | acceptée, lecture la plus prudente du principe 3 |
| V1-06 | Un élément `pret` peut être rattaché de nouveau | acceptée, `specs/08 §3.3` le permet |
| V1-06 | `app.py` modifié pour inclure le routeur | acceptée, `specs/08 §1.1` l'exige |
| V1-06 | Cache de moteurs de `db/base.py` remplacé | acceptée, voir la section qui lui est consacrée |
| V1-07 | Routes des brouillons ajoutées une à une | acceptée, documentée et minimale |
| V1-07 | Zone copiée : objet, ligne vide, corps | acceptée |
| V1-08 | « Les contrats V1-08 n'ont pas tourné dans ma session » | à corriger, probablement fausse : la session porte la variable `CABINET_TICKET` et la boucle a vérifié les 30 contrats avant de commiter |
| V1-C1 | Identifiants français dans les nouveaux tests pour suivre le fichier existant | refusée. Le code neuf s'écrit en anglais sans exception |

Choix pris sans note (R4) : acceptables sur le fond, à noter à l'avenir.

## Conduite de l'exécutant

- Qwen n'a tenté aucun commit et n'a touché aucun fichier protégé.
- 21 commandes refusées sur onze sessions. Trois fois, la commande refusée a été obtenue autrement (R2). La règle est pourtant écrite dans `docs/EXECUTANT.md` depuis la phase A.
- Les pensées et messages intermédiaires de Qwen sont en anglais, contre la règle 6 de `CLAUDE.md`. Ses notes et résumés finaux sont en français.
- La première tentative de V1-02 a duré 4 h, la limite, sans écrire une ligne. Le journal montre des silences de 53, 86 et 64 minutes entre deux appels d'outil, pendant que Qwen relisait la spécification et les sources de SQLAlchemy. La seconde tentative a livré le ticket en 3 h.

## Ce que la revue a changé dans la chaîne

Tous les réglages sont des commits `[V1-chaine]` ou `[V1-tests]`. Aucun ne touche le code de l'application.

- Pendant la vague : jeton de la page de suivi repris après redémarrage, délai de session réglable à chaud (`.chaine/delai_session`), voyant de marche et thème sombre sur la page de suivi, lancement des processus par launchd (`scripts/lancer.sh`), envoi des commits vers GitHub par un crochet `post-commit`.
- Après la vague :
  - la garde ne regarde que les commits de ticket (`26ebc36`).
  - un angle de relecture qui réserve sans aucun constat compte comme incomplet (`9746463`). L'angle Conduite de V1-02 avait été coupé par un filtre de sécurité et le ticket était sorti « ok ». Relu de nouveau, V1-02 sort « réserves », avec le contournement de R2 en constat majeur.
  - la limite de fichiers ouverts des tâches launchd est portée à 4096 (`26db180`).
  - la fonction auxiliaire du contrat V1-C1 est renommée (`d0b5d08`).

## Pour l'architecte

Ce que l'exécutant fait bien :

- Il tient tous les critères d'acceptation, neuf tickets sur neuf, souvent avec les messages de la spécification mot pour mot.
- Il trouve la cause d'un échec au lieu de le masquer. Pour V1-06, il a remonté le « too many open files » jusqu'au cache de moteurs et l'a documenté.
- Il a corrigé les réserves R1 à R4 de la vague 0 dans `src/`.
- Il choisit la lecture la plus prudente quand la spécification est ambiguë (consentement exigé à chaque rattachement).

Ce qu'il rate de façon répétée :

- Les identifiants en anglais, dès qu'un test automatique ne les vérifie pas. Il a renommé les 45 noms de la liste, puis il a écrit des noms français dans ses nouveaux tests et dans V1-04.
- Les refus de permission. Il ne relance plus la même commande, mais il en contourne l'effet.
- Les notes de ticket, absentes pour trois tickets sur neuf.
- Le temps. Le débit moyen est tombé à 6 tokens par seconde, contre 14 en vague 0. Hypothèse, non vérifiée : les contextes sont bien plus longs (19,7 millions de tokens lus en entrée sur la vague), et une partie de la vague a tourné sur batterie.

Ce qu'il faudrait changer dans la forme des tickets et de la chaîne :

- Un test de contrat transversal sur la langue de tous les identifiants définis dans `src/` et `tests/unit/`, pas seulement sur une liste de noms.
- Un test de contrat transversal qui interdit `async def` dans `web/routes/`.
- `specs/01 §5` fixe `line-length = 100`, mais la sélection de règles de ruff n'inclut pas `E501`. Trois lignes dépassent 100 caractères sans que `make check` le voie. Il faut ajouter `E501` à la sélection ou retirer la règle.
- Rappeler en tête de chaque ticket : une note par choix, même petit.
- Des tickets plus courts pour les sujets où Qwen lit beaucoup avant d'écrire (V1-02 : quatorze tables d'un coup).

## Mesures

La boucle de la vague 1 a démarré le 24.11 à 23:23 et s'est arrêtée seule le 26.11 à 13:51, soit 38 h 28 de temps écoulé. Depuis le passage à launchd le 25.11 à 14:13, elle a tourné sans interruption.

| Mesure | Valeur |
| --- | --- |
| Tickets faits | 9 sur 9 |
| Tickets terminés à la première tentative | 5 (V1-03, V1-04, V1-05, V1-07, V1-08) |
| Tentatives perdues à cause de la chaîne | 4 : V1-C1 une fois (Mac en veille, passerelle arrêtée), V1-01 deux fois (processus emportés avec la session du relecteur), V1-06 une fois (limite de fichiers de launchd) |
| Tentatives perdues à cause de Qwen | 1 : V1-02, délai de 4 h atteint sans écriture |
| Tickets bloqués | 0 |
| Interventions sur le travail de Qwen | 0 |
| Interventions sur la chaîne pendant la vague | 4, notées au journal, dont une à la demande de Thomas (délai porté à 240 minutes) |
| Sessions menées jusqu'au bout | 11, pour 24 h 37 de calcul |
| Fichiers protégés restaurés par la garde | 0 |
| Débit de Qwen | 6,1 tokens produits par seconde en moyenne |
| Coût des relecteurs automatiques | 12,34 $ pour dix relectures, dont 1,13 $ pour la seconde relecture de V1-02 |

| Ticket | Tentatives | Durée de la session retenue | Tours | Erreurs d'outil | Refus | Contrats réussis | Relecture automatique |
| --- | --- | --- | --- | --- | --- | --- | --- |
| V1-C1 | 2 | 1 h 00 | 42 | 2 | 2 | 8 | ok (3 mineurs, 12 remarques) |
| V1-01 | 3 | 1 h 07 | 40 | 3 | 2 | 41 | ok (4 mineurs, 8 remarques) |
| V1-02 | 2 | 2 h 59 | 66 | 5 | 2 | 68 | réserves à la seconde relecture (1 majeur, 2 mineurs, 4 remarques) |
| V1-03 | 1 | 1 h 30 | 40 | 5 | 4 | 99 | réserves (1 majeur, 1 mineur, 4 remarques) |
| V1-04 | 1 | 2 h 34 | 96 | 2 | 1 | 34 | réserves (3 majeurs, 2 mineurs, 2 remarques) |
| V1-05 | 1 | 2 h 21 | 55 | 2 | 1 | 38 | ok (2 mineurs, 5 remarques) |
| V1-06 | 2 | 1 h 08 | 39 | 4 | 4 | 49 | réserves (5 majeurs, 4 remarques) |
| V1-07 | 1 | 2 h 15 | 79 | 3 | 3 | 52 | ok (5 mineurs, 4 remarques) |
| V1-08 | 1 | 2 h 32 | 70 | 2 | 1 | 30 | ok (5 mineurs, 4 remarques) |

Les cinq majeurs de V1-06 portent tous sur le cache de moteurs, écart accepté plus haut. Thomas a trié les constats confirmés de V1-C1 à V1-07 et les a tous marqués « à corriger ». Les neuf constats de V1-08 et les nouveaux constats de V1-02 attendent son tri.

Projection, à prendre comme une estimation : les tickets de la vague 1 ont demandé en moyenne 2 h 45 de calcul chacun. À ce rythme, les 16 tickets des lots 2 et 3 demandent environ 45 heures de calcul et 20 $ de relecture. Les tickets du lot 2 appellent le modèle et seront plus lents à vérifier.

## Suite proposée

1. Thomas joue la recette du lot 1 et donne ou non son accord pour la fusion de `vague-1` dans `main`.
2. Thomas trie les constats de V1-08 et de la seconde relecture de V1-02 dans l'onglet Revues de la page de suivi.
3. Thomas tranche Q1 et Q2 de `revues/vague-1-questions.md`, qui touchent le lot 2.
4. Phase A de la vague 2 : un ticket de correction V2-C1 en tête (R1 à R9 et les constats triés), l'alignement de `specs/05 §2.1` sur le cache de moteurs, les deux tests transversaux proposés plus haut et la règle `E501`.

## Accord de Thomas

Le 26.11.2026, Thomas a répondu « fait tout » à la liste de la suite proposée. Il donne ainsi son accord à la fusion et délègue au relecteur le tri des constats restants, les réponses à Q1 et Q2, puis la phase A et le lancement de la vague 2. La recette du lot 1 jouée par le relecteur tient lieu de recette. Avant un usage réel, Thomas doit encore vérifier que son agenda s'exporte en ICS (`specs/00 §4.13`).

Tri des seize constats restants : trois à corriger dans V2-C1 (verrou du pipeline peu testé, `ConfigError` non rattrapée par `cabinet run`, annotations des tests de V1-02), neuf acceptés, quatre hors sujet. Chaque décision porte une note dans la page de suivi.
