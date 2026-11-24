# Interface web

Pages, routes et formulaires de la page locale, et cycle de vie des brouillons. Les services appelés par les routes sont décrits dans `specs/05` à `specs/07`. Les codes HTTP et les classes d'erreur sont dans `specs/01 §6`.

## 1. Application

### 1.1 `create_app`

```python
# src/cabinet/web/app.py
def create_app() -> FastAPI: ...
```

1. Créer l'application avec `FastAPI(title="Cabinet", docs_url=None, redoc_url=None, openapi_url=None)`. Les pages de documentation de FastAPI chargent des scripts depuis un CDN : elles sont désactivées.
2. Appeler `init_db()` (à partir de la tâche V0-03).
3. Poser les deux gardes de la section 1.3 (à partir de la tâche V1-05).
4. Monter `web/static/` sous `/static` et déclarer les gabarits de `web/templates/` (`Path(__file__).parent`).
5. Inclure un routeur par module de `web/routes/`.

`make run` lance `create_app` en mode fabrique sur `127.0.0.1:8765` (`specs/03 §6`).

### 1.2 Conventions des routes

- Une route est une fonction synchrone (`def`). Elle ouvre sa session par `with get_session() as session` et la valide avant de répondre.
- Un `GET` rend un gabarit Jinja2. Un `POST` reçoit un formulaire HTML (`Form(...)`), agit, puis redirige par le code 303.
- Une redirection porte un message facultatif dans le paramètre `message` de l'adresse, encodé par `urllib.parse.quote`. `base.html` l'affiche en tête de page, échappé par Jinja2.
- Une erreur 400, 404 ou 409 rend le gabarit `error.html` avec le code et le message en français. Elle ne modifie jamais la base.
- Les routes relisent les réglages par `load_settings()` à chaque requête. Une `ConfigError` rend `error.html` avec le code 500 et le message de l'erreur.
- Aucune logique métier ne vit dans une route : elle appelle un service ou un agent.
- Les dates s'affichent `JJ.MM.AAAA`, les heures `HH:MM` dans `LOCAL_TZ`. Les champs de formulaire de date sont de type `date` et transmettent `AAAA-MM-JJ`.

### 1.3 Gardes de l'application

Lier le serveur à `127.0.0.1` n'empêche pas une page web ouverte dans le navigateur de l'utilisateur d'envoyer une requête à `http://127.0.0.1:8765`. Deux gardes ferment cette porte. Elles ne figuraient pas dans le kit.

| Garde | Règle | Réponse en cas de refus |
| --- | --- | --- |
| Hôte | `TrustedHostMiddleware` de starlette, avec les hôtes `127.0.0.1`, `localhost` et `testserver`. Ce dernier est l'hôte du client de test | 400 |
| Origine | Pour toute requête `POST` qui porte un en-tête `Origin`, sa valeur doit commencer par `http://127.0.0.1:` ou `http://localhost:`. Une requête sans cet en-tête passe | 403 |

La garde d'hôte empêche la lecture des pages par un nom de domaine qui pointerait sur l'adresse de bouclage. La garde d'origine empêche un formulaire d'un autre site de déclencher un rattachement, une validation ou un passage du pipeline.

### 1.4 Mise en page

- `base.html` porte la langue (`lang="fr"`), le titre, la navigation, le message de redirection et un bloc de contenu.
- La navigation a huit liens, dans cet ordre : Tableau de bord (`/`), Entrée (`/entree`), Réunions (`/reunions`), Actions (`/actions`), Imputations (`/imputations`), Brouillons (`/brouillons`), Journal (`/journal`), Réglages (`/reglages`). Un lien vers une page qui n'existe pas encore est présent quand même.
- Couleurs : fond `#FFFAF0`, texte `#0F0E2B`, accent `#1F24E9`, déclarées en variables CSS dans `app.css`. Police du système (`font-family: system-ui, sans-serif`).
- Aucune ressource externe. Aucun fichier de `templates/` ni de `static/` ne contient `http://` ou `https://`.
- Aucun framework JavaScript. `app.js` ne contient que la copie dans le presse-papiers (section 7.4).
- Un texte Markdown (compte rendu, fiche) s'affiche tel quel dans un bloc préformaté. Aucune bibliothèque de rendu Markdown n'est dans la liste des dépendances.
- Chaque champ de formulaire a un libellé. Chaque bouton dit ce qu'il fait (« Valider le compte rendu », pas « OK »).

## 2. Tableau de bord, santé et réglages

### 2.1 `GET /`

| Bloc | Contenu |
| --- | --- |
| Réunions à venir | réunions dont la date est comprise entre `clock.today()` et sept jours plus tard, bornes incluses, par date puis par heure. Pour chacune : date, heure si elle existe, titre, marque « suivie » si sa série l'est |
| Compteurs | quatre nombres, tableau ci-dessous |
| Échéance des imputations | section 2.2. À partir de V3-07, suivie du nombre d'anomalies bloquantes de la dernière capture relue |
| Traitement | bouton « Traiter maintenant », à partir de V1-08 |

| Compteur | Définition |
| --- | --- |
| Fichiers à traiter | `inbox_item` à l'état `a_rattacher` ou `consentement_requis` |
| Brouillons à relire | `draft` à l'état `brouillon` |
| Actions ouvertes | `action_item` à l'état `ouvert`. À partir de V2-07, seulement celles d'un compte rendu validé |
| Comptes rendus à relire | réunions dont le compte rendu courant est à l'état `brouillon` |

La page répond 200 sur une base vide.

### 2.2 Échéance

Avec `d`, le nombre de jours entre `clock.today()` et `cra.deadline` :

| Cas | Texte affiché |
| --- | --- |
| réglage vide | rien |
| `d` supérieur à 0 | « J-{d} », par exemple « J-3 » à trois jours |
| `d` égal à 0 | « aujourd'hui » |
| `d` inférieur à 0 | « échéance dépassée » |

### 2.3 `POST /traiter`

1. Charger les réglages et construire le pipeline (`specs/05 §3.3`).
2. Lancer `pipeline.run(get_session)`.
3. Sur `PipelineBusy`, rediriger vers `/` avec le message « Un traitement est déjà en cours. »
4. Sinon rediriger vers `/` avec un résumé : « Traitement terminé. » suivi d'un fragment par agent, `{nom} : {traités}` et, s'il y a lieu, `({n} erreur(s))` ou `en échec`.

La requête dure le temps du passage, soit plusieurs minutes dès qu'un compte rendu est à extraire (`specs/00 §4.2`). Les autres pages restent consultables pendant ce temps. Pour un traitement long, la commande `cabinet run` dans un terminal est la voie conseillée. Le passage du traitement en arrière-plan est une évolution à décider après la recette du lot 2.

### 2.4 `GET /sante` et `GET /reglages`

`GET /sante` renvoie le code 200 et `{"statut": "ok", "version": "0.1.0"}`, la version étant lue dans les métadonnées du paquet.

`GET /reglages` affiche en lecture seule le chemin du répertoire de données et toutes les valeurs de `Settings`, section par section. La variable `CABINET_ALLOW_REMOTE_LLM` n'y est pas mentionnée.

## 3. Entrée

### 3.1 `suggest_meetings`

```python
# src/cabinet/services/matching.py
def suggest_meetings(session: Session, item: InboxItem, limit: int = 5) -> list[tuple[Meeting, float]]: ...
```

1. Le jour de référence est `item.name_date`. À défaut, c'est le jour de `item.detected_at` dans `LOCAL_TZ`.
2. Prendre les réunions dont la date est ce jour.
3. Comparer le nom du fichier au titre de chaque réunion avec `difflib.SequenceMatcher(None, a, b).ratio()`. `a` est le nom sans extension ni préfixe de date, `b` le titre. Les deux sont mis en minuscules, sans accents, tirets et tirets bas remplacés par une espace.
4. Renvoyer au plus `limit` couples (réunion, score), par score décroissant puis par `id`.

Le kit disait aussi que la date de modification du fichier servait de repli. C'est la date de détection qui joue ce rôle : elle est en base, la date de modification ne l'est pas.

### 3.2 `GET /entree`

La page liste les éléments par état, dans cet ordre : `a_rattacher`, `consentement_requis`, `pret`, `erreur`, `traite`, `ignore`. Les deux derniers groupes sont limités aux 50 éléments les plus récents.

Pour chaque élément : nom du fichier, nature, date lue dans le nom, réunion rattachée, message d'erreur. Un élément `a_rattacher` ou `consentement_requis` porte un formulaire de rattachement : ses suggestions en boutons radio, deux champs pour une réunion hors agenda (titre, date), et pour un audio la case « J'ai l'accord des participants pour cet enregistrement ».

### 3.3 `POST /entree/{id}/rattacher`

Champs : `meeting_id`, ou bien `titre` et `date`. `consentement` pour un audio.

1. Élément inconnu : 404.
2. État autre que `a_rattacher`, `consentement_requis` ou `pret` : 409. C'est le cas d'un élément `traite`.
3. Nature `app_capture_text`, `app_capture_image` ou `inconnu` : 409. Une capture ne se rattache pas à une réunion.
4. Nature `audio` sans la case cochée : 400, et rien ne change. C'est le premier contrôle du principe 3.
5. Avec `meeting_id` : la réunion doit exister, sinon 404.
6. Sans `meeting_id` : `titre` et `date` sont obligatoires, sinon 400. Chercher la série de clé `series_key(titre)`, la créer si elle manque, puis créer une réunion sans évènement.
7. Écrire `item.meeting_id`. Pour un audio, passer `consent_confirmed` à vrai et `consent_at` à `clock.now()`.
8. Passer l'élément à `pret` et rediriger vers `/entree`.

### 3.4 `POST /entree/{id}/ignorer` et `POST /entree/{id}/reessayer`

`ignorer` passe l'élément à `ignore`, sauf s'il est `traite` (409).

`reessayer` n'agit que sur un élément `erreur` (409 sinon). Il vide `error` et remet l'élément dans l'état d'où il peut repartir : `pret` s'il a tout ce qu'il faut (une réunion pour un transcript, des notes ou un audio, et le consentement pour un audio, rien de plus pour une capture), sinon `consentement_requis` pour un audio et `a_rattacher` pour le reste.

Tests : `tests/unit/test_inbox_page.py`. Cas attendus : suggestions du bon jour triées par similarité, rattachement d'un transcript à une réunion existante, audio sans la case refusé par 400 et laissé à `consentement_requis`, audio avec la case passé à `pret` avec `consent_at`, réunion hors agenda créée avec sa série, élément ignoré, élément `traite` refusé par 409, élément en erreur rejoué.

## 4. Réunions

### 4.1 `GET /reunions`

Réunions dont la date est comprise entre 30 jours avant `clock.today()` et 14 jours après, de la plus récente à la plus ancienne. Pour chacune : date, heure, titre, série avec sa bascule « suivie », état du compte rendu (« aucun », « brouillon » avec le nombre d'éléments à vérifier, « validé ») et état de la fiche (« aucune », « prête »).

### 4.2 `GET /reunions/{id}`

| Bloc | Contenu |
| --- | --- |
| En-tête | titre, date, heure, lieu, participants, série |
| Sources | transcripts de la réunion (source, nom du fichier, nombre de segments), et éléments rattachés pas encore lus |
| Fiche de préparation | `content_md` si une fiche existe, et le bouton « Régénérer la fiche » |
| Compte rendu | le compte rendu courant : résumé, décisions, actions, points ouverts, prochaine séance, numéro de version |

Chaque décision et chaque action affiche son extrait. Un élément dont `verified` est faux est marqué « à vérifier » et porte le bouton « Confirmer ». Sur un brouillon, chaque élément a ses boutons de modification et de suppression, et la page porte « Régénérer » et « Valider le compte rendu ». Sur un compte rendu validé, aucun de ces boutons n'apparaît.

La page répond 200 pour une réunion sans source, et 404 pour une réunion inconnue.

### 4.3 Corrections et validation

Toutes ces routes agissent sur le compte rendu courant de la réunion et répondent 409 s'il est `valide`. Chacune recalcule `content_md` (`specs/06 §7`) et redirige vers `/reunions/{id}`.

| Route | Champs | Effet |
| --- | --- | --- |
| `POST /reunions/{id}/resume` | `summary` | modifie le résumé |
| `POST /reunions/{id}/decisions/{did}` | `text`, `quote` | modifie une décision. Si l'extrait change, relance son contrôle (`specs/06 §8.1`) |
| `POST /reunions/{id}/decisions/{did}/confirmer` | | passe `verified` à vrai |
| `POST /reunions/{id}/decisions/{did}/supprimer` | | supprime la décision |
| `POST /reunions/{id}/actions/{aid}` | `description`, `owner`, `due_date`, `quote` | modifie une action. Même règle pour l'extrait |
| `POST /reunions/{id}/actions/{aid}/confirmer` | | passe `verified` à vrai |
| `POST /reunions/{id}/actions/{aid}/supprimer` | | supprime l'action |
| `POST /reunions/{id}/regenerer` | | `MinutesAgent.regenerate`, dans `record_run(session, "minutes", "reunion:{id}")` |
| `POST /reunions/{id}/valider` | | `validate_minutes`, avec le réglage `delete_audio_after_validation` |

`valider` répond 409 sur `ValidationBlocked`, avec son message. `regenerer` redirige avec le message « La régénération a échoué : {message}. » sur `LLMError`, et l'ancien compte rendu reste en place.

Le kit disait à la fois qu'une action d'un compte rendu validé n'est plus modifiable et que la page Actions en modifie le responsable et l'échéance. La règle retenue sépare le contenu du suivi. Après validation, le contenu d'une action (description, extrait, confirmation, suppression) est figé. Son suivi (responsable, échéance, état) reste modifiable depuis la page Actions (section 5).

### 4.4 Séries et fiches

| Route | Effet |
| --- | --- |
| `POST /series/{id}/suivre` | bascule `series.followed`, redirige vers `/reunions` |
| `POST /reunions/{id}/fiche/regenerer` | `PreparationAgent.regenerate`, dans `record_run(session, "preparation", "reunion:{id}")`, redirige vers `/reunions/{id}` |

Tests : `tests/unit/test_meeting_page.py` et `tests/unit/test_preparation.py`. Cas attendus : `GET /reunions` et `GET /reunions/{id}` à 200 y compris sans source, corrections enregistrées, 409 sur un compte rendu validé, bascule « suivie », régénération de la fiche.

## 5. Actions

### 5.1 `GET /actions`

La page liste les actions issues d'un compte rendu validé. Elle ne montre jamais une action d'un brouillon.

| Paramètre | Effet |
| --- | --- |
| `etat` | `ouvert` par défaut, ou `fait` pour voir les actions closes |
| `serie` | identifiant d'une série |
| `responsable` | valeur exacte de `owner` |
| `retard` | `1` pour ne garder que les actions dont l'échéance est antérieure à `clock.today()` |

Tri par échéance croissante, les actions sans échéance en dernier, puis par `id`.

Sous la liste, la page affiche par série les décisions des trois dernières réunions validées.

### 5.2 Suivi d'une action

Ces routes n'agissent que sur une action d'un compte rendu validé, et répondent 409 sinon.

| Route | Champs | Effet |
| --- | --- | --- |
| `POST /actions/{id}` | `owner`, `due_date` | modifie le responsable et l'échéance. Un champ vide donne `None` |
| `POST /actions/{id}/clore` | | passe l'action à `fait` |
| `POST /actions/{id}/rouvrir` | | la remet à `ouvert` |

Tests : `tests/unit/test_actions.py`. Cas attendus : seules les actions de comptes rendus validés, tri avec les actions sans échéance à la fin, filtres par série, par responsable et « en retard », clôture puis réouverture avec effet sur le compteur du tableau de bord, responsable et échéance enregistrés.

## 6. Imputations

### 6.1 `GET /imputations`

- Les captures sont groupées par période, de la plus récente à la plus ancienne. Une capture sans période est rangée sous « Période à préciser ».
- Pour chaque capture : source, état (« à relire » ou « relue »), nombre d'anomalies bloquantes et à vérifier, lien vers l'écran de vérification.
- Pour la dernière capture relue de chaque période (plus grand `verified_at`), un tableau donne par personne du référentiel : jours attendus, jours saisis, anomalies, état du brouillon de relance (« aucun », « brouillon », « validé », « rejeté », « envoyé »).
- Si le référentiel est absent ou invalide, le tableau est remplacé par le message de la `ConfigError`.
- Un formulaire donne la date d'échéance (section 6.3).

La page répond 200 sur une base vide.

### 6.2 Écran de vérification

`GET /imputations/captures/{id}` affiche la période, le tableau des lignes (personne, code, jours), un formulaire d'ajout de ligne, les lignes ignorées à la lecture (`inbox_item.error`) et le bouton « Marquer relue ». Une personne absente du référentiel (comparaison par `person_key`) est signalée, avec une liste déroulante des personnes du référentiel.

| Route | Champs | Effet |
| --- | --- | --- |
| `POST /imputations/captures/{id}/periode` | `period` | écrit la période. Format autre que `AAAA-MM` : 400 |
| `POST /imputations/captures/{id}/lignes` | `person`, `project_code`, `days` | ajoute une ligne |
| `POST /imputations/captures/{id}/lignes/{lid}` | `person`, `project_code`, `days` | modifie une ligne |
| `POST /imputations/captures/{id}/lignes/{lid}/supprimer` | | supprime une ligne |
| `POST /imputations/captures/{id}/correspondance` | `source`, `cible` | renomme `source` en `cible` sur toutes les lignes de la capture. `cible` doit être un nom du référentiel, sinon 400 |
| `POST /imputations/captures/{id}/relire` | | exige une période renseignée, sinon 400. Passe `verified` à vrai et `verified_at` à `clock.now()` |

Le champ `days` accepte la virgule ou le point. Une valeur illisible ou négative donne 400.

Chacune des cinq premières routes, appliquée à une capture relue, remet `verified` à faux, vide `verified_at` et `checked_at`, et supprime les anomalies de la capture. Chaque `POST` réussi redirige vers la page de la capture.

### 6.3 `POST /imputations/echeance`

Champ `date`. Une date valide appelle `set_cra_deadline(date)`, un champ vide appelle `set_cra_deadline(None)` (`specs/03 §3.2`). Une valeur illisible donne 400. La route redirige vers `/imputations`.

Tests : `tests/unit/test_capture_page.py` et `tests/unit/test_timesheets_page.py`. Cas attendus : écran de vérification à 200 avec toutes les lignes, ligne modifiée, ajoutée et supprimée, correspondance qui renomme toutes les lignes d'un nom, « relue » refusée sans période, `verified` et `verified_at` fixés, modification d'une capture relue qui la rend non relue et supprime ses anomalies, page d'ensemble à 200 sur une base vide, états « à relire » et « relue », jours attendus et saisis par personne, échéance enregistrée sans toucher aux autres lignes du fichier.

## 7. Brouillons

### 7.1 Service

```python
# src/cabinet/services/drafts.py
class TransitionError(Exception): ...

def create_draft(session, kind, recipients, subject, body, meeting_id=None, capture_id=None) -> Draft: ...
def update_draft(session, draft_id, subject, body, recipients) -> Draft: ...
def set_status(session, draft_id, new_status) -> Draft: ...
def mailto_link(draft: Draft) -> str: ...
```

- `create_draft` crée un brouillon à l'état `brouillon`, avec `created_at = clock.now()`.
- `update_draft` n'agit que sur un brouillon à l'état `brouillon`. Sinon elle lève `TransitionError`.
- `set_status` n'accepte que les trois transitions du diagramme de `specs/02 §2.14` : `brouillon` vers `valide`, `brouillon` vers `rejete`, `valide` vers `envoye_a_la_main`. Toute autre lève `TransitionError`.

### 7.2 Validation et export

Le passage à `valide` écrit le fichier `brouillons/AAAA-MM-JJ_{kind}_{id}.md`, où la date est `clock.today()` :

```markdown
# {objet}

Destinataires : {adresses séparées par une virgule}

{corps}
```

### 7.3 `mailto_link`

```python
"mailto:" + ",".join(quote(a, safe="@") for a in draft.recipients) \
    + "?subject=" + quote(draft.subject, safe="") + "&body=" + quote(draft.body, safe="")
```

`quote` est `urllib.parse.quote`. Les accents sont encodés en UTF-8 et un retour à la ligne devient `%0A`. Décoder le lien redonne l'objet et le corps d'origine.

Un lien `mailto:` a une longueur pratique limitée, qui dépend de la messagerie. Un compte rendu long peut la dépasser : le bouton « Copier » sert alors de repli.

### 7.4 Page

`GET /brouillons` liste les brouillons par état, dans cet ordre : `brouillon`, `valide`, `envoye_a_la_main`, `rejete`. Pour chacun : nature, destinataires, objet, corps, date de création.

| Route | Champs | Effet |
| --- | --- | --- |
| `POST /brouillons/{id}` | `subject`, `body`, `recipients` (adresses séparées par une virgule) | `update_draft` |
| `POST /brouillons/{id}/valider` | | `set_status(…, "valide")` et export |
| `POST /brouillons/{id}/rejeter` | | `set_status(…, "rejete")` |
| `POST /brouillons/{id}/envoye` | | `set_status(…, "envoye_a_la_main")` |

Une `TransitionError` donne 409. Un identifiant inconnu donne 404. Chaque `POST` réussi redirige vers `/brouillons`.

Le lien « Ouvrir dans la messagerie » (`mailto_link`) et le bouton « Copier » n'apparaissent qu'à l'état `valide`. Le bouton « Copier » porte l'attribut `data-copy` avec l'identifiant de la zone de texte à copier. `app.js` appelle `navigator.clipboard.writeText` et ne fait rien d'autre.

C'est l'utilisateur qui envoie, depuis sa messagerie. « Marquer envoyé » constate ce geste après coup.

Tests : `tests/unit/test_drafts.py`. Cas attendus : les trois transitions permises, `rejete` vers `valide` et `brouillon` vers `envoye_a_la_main` refusées par 409, fichier d'export créé à la validation avec l'objet et le corps, modification d'un brouillon validé refusée par 409, lien `mailto:` absent de la page avant validation, lien décodé qui redonne l'objet et le corps avec accents et retours à la ligne, aucune occurrence de `smtplib`, `sendMail` ni `send_message` dans `src/`.

## 8. Journal

`GET /journal` affiche les 100 dernières exécutions d'agent, la plus récente en premier (tri par `started_at` puis `id` décroissants). Pour chacune : agent, cible, état, heure de début, durée (« en cours » sans `ended_at`), modèle, tokens, erreur.

La page répond 200 sur une base vide et n'affiche jamais plus de 100 lignes.

## 9. Table des routes

| Méthode et adresse | Module | Tâche |
| --- | --- | --- |
| `GET /sante` | `web/app.py` | V0-01 |
| `GET /journal` | `routes/journal.py` | V0-03 |
| `GET /`, `GET /reglages` | `routes/dashboard.py` | V1-05 |
| `GET /entree`, `POST /entree/{id}/rattacher`, `/ignorer`, `/reessayer` | `routes/inbox.py` | V1-06 |
| `GET /brouillons`, `POST /brouillons/{id}`, `/valider`, `/rejeter`, `/envoye` | `routes/drafts.py` | V1-07 |
| `POST /traiter` | `routes/dashboard.py` | V1-08 |
| `GET /reunions`, `GET /reunions/{id}`, routes de la section 4.3 | `routes/meetings.py` | V2-06 |
| `GET /actions`, `POST /actions/{id}`, `/clore`, `/rouvrir` | `routes/actions.py` | V2-07 |
| `POST /series/{id}/suivre`, `POST /reunions/{id}/fiche/regenerer` | `routes/meetings.py` | V2-08 |
| `GET /imputations/captures/{id}`, routes de la section 6.2 | `routes/timesheets.py` | V3-04 |
| `GET /imputations`, `POST /imputations/echeance` | `routes/timesheets.py` | V3-07 |

Gabarits : `base.html`, `error.html`, `journal.html`, `dashboard.html`, `settings.html`, `inbox.html`, `drafts.html`, `meetings.html`, `meeting.html`, `actions.html`, `capture.html`, `timesheets.html`.

## 10. Ce qu'un implémenteur doit garder en tête

- Les adresses des routes sont en français et contractuelles. Les tests de contrat les appellent telles quelles.
- Un `POST` ne rend jamais une page : il redirige par 303, ou répond par une erreur 4xx sans rien modifier.
- La case de consentement se contrôle côté serveur. Un formulaire sans la case reçoit 400, même si le navigateur a été contourné.
- Le lien `mailto:` et le bouton « Copier » n'existent dans la page qu'à l'état `valide`. Les masquer par CSS ne suffit pas.
- Aucune adresse `http://` ni `https://` dans `templates/` et `static/`. La documentation automatique de FastAPI reste désactivée.
- Tout texte venu d'un fichier ou du modèle passe par l'échappement de Jinja2. Ne jamais utiliser le filtre `safe` sur un tel texte.
- Les réunions « des 7 prochains jours » et l'échéance se calculent avec `clock.today()`, que les tests fixent.
- Le contenu d'une action se fige à la validation, pas son suivi. Les deux familles de routes ne se remplacent pas l'une l'autre.
- Toute modification d'une capture relue la rend non relue. Ce comportement vit dans chaque route de modification, pas dans le gabarit.
