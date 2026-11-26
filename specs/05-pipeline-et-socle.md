# Pipeline et socle

Base, journal, orchestrateur et les deux premiers agents (ingestion et agenda). Le contrat commun d'un agent est dans `specs/01 §3.3`. Les tables sont dans `specs/02`. Les chemins et les réglages sont dans `specs/03`.

## 1. Agents de la version 1

| Ordre | Nom | Classe | Module | Dépendances du constructeur | Lot |
| --- | --- | --- | --- | --- | --- |
| 1 | `ingestion` | `IngestionAgent` | `agents/ingestion.py` | `inbox: Inbox` | 1 |
| 2 | `agenda` | `AgendaAgent` | `agents/agenda.py` | `calendar: CalendarSource` | 1 |
| 3 | `transcripts` | `TranscriptsAgent` | `agents/transcripts.py` | aucune | 2 |
| 4 | `transcription` | `TranscriptionAgent` | `agents/transcription.py` | `transcriber: Transcriber` | 2 |
| 5 | `minutes` | `MinutesAgent` | `agents/minutes.py` | `llm: LLM`, `context_tokens: int = 64000` | 2 |
| 6 | `preparation` | `PreparationAgent` | `agents/preparation.py` | `llm: LLM` | 2 |
| 7 | `timesheets` | `TimesheetsAgent` | `agents/timesheets.py` | `llm: LLM \| None = None`, `rules: RulesSettings = RulesSettings()`, `deadline: date \| None = None` | 3 |

Chaque classe porte l'attribut `name` de la deuxième colonne et la méthode `run(self, session) -> AgentOutcome`.

## 2. Base et journal

### 2.1 `db/base.py`

```python
class Base(DeclarativeBase): ...
class TZDateTime(TypeDecorator): ...          # specs/02 §1.1

def get_engine() -> Engine: ...
def get_session() -> Session: ...
def init_db() -> None: ...
```

Algorithme de `get_engine` :

1. Déterminer le fichier de la base : `paths().db_file`. Avant la tâche V1-01, le calculer sur place à partir de `CABINET_HOME`, avec `~/Cabinet` en repli.
2. Créer le dossier parent s'il manque.
3. Renvoyer le moteur de ce chemin. Un dictionnaire privé garde un seul moteur à la fois : un appel sur le même chemin renvoie le même moteur, un appel sur un autre chemin libère le précédent (`Engine.dispose()`) et crée le nouveau :

   ```python
   create_engine(f"sqlite:///{chemin}", connect_args={"timeout": 30})
   ```

   avec un écouteur `connect` qui exécute `PRAGMA foreign_keys=ON` sur chaque nouvelle connexion.

Le chemin est résolu à chaque appel. Changer `CABINET_HOME` entre deux appels donne donc deux moteurs et deux bases. Ce cache est le seul état global toléré hors de la configuration.

Pourquoi un seul moteur (décision du 26.11.2026, `revues/vague-1.md`) : chaque test ouvre une base dans un répertoire neuf. Un cache sans borne gardait un moteur et ses connexions par test, et la suite a épuisé la limite de fichiers ouverts du processus (`OSError: [Errno 24]`) pendant V1-06.

`get_session()` renvoie `Session(get_engine(), expire_on_commit=False)`. La session est neuve à chaque appel. L'appelant la ferme, de préférence par `with get_session() as session`.

`init_db()` importe `cabinet.db.models`, puis appelle `Base.metadata.create_all(get_engine())`. Un second appel ne lève pas d'erreur et ne perd aucune donnée. `create_app()` et `cabinet run` l'appellent au démarrage.

### 2.2 `record_run`

```python
# src/cabinet/agents/journal.py
@contextmanager
def record_run(session: Session, agent: str, target_ref: str = "") -> Iterator[AgentRun]: ...
```

Algorithme :

1. Créer une ligne `agent_run` à l'état `en_cours`, avec `started_at = clock.now()`. L'ajouter à la session et valider aussitôt (`session.commit()`). La ligne est donc visible par un autre processus pendant l'exécution.
2. Donner la ligne à l'appelant (`yield`).
3. En sortie normale : passer la ligne à `ok`, renseigner `ended_at`, valider. Le travail que l'appelant a laissé en attente dans la session est validé du même coup.
4. Sur toute exception, interruption comprise : annuler le travail en attente (`session.rollback()`), relire la ligne par son identifiant, la passer à `erreur`, écrire dans `error` le message de l'exception (ou le nom de sa classe si le message est vide, 2 000 caractères au plus), renseigner `ended_at`, valider, puis relancer l'exception.

La trace d'une erreur survit donc à l'annulation que ferait l'appelant après coup. C'est la lecture du principe 5 : tout est journalisé, y compris en cas d'erreur.

`record_run` s'ouvre toujours au début d'une unité de travail, sur une session qui n'a rien en attente. Trois appelants s'en servent :

| Appelant | `agent` | `target_ref` |
| --- | --- | --- |
| `Pipeline.run`, pour chaque agent | nom de l'agent | `""` |
| Régénération d'un compte rendu depuis la page Réunion | `minutes` | `reunion:{id}` |
| Régénération d'une fiche depuis la page Réunion | `preparation` | `reunion:{id}` |

### 2.3 Modèle et tokens au journal

Après un `agent.run` réussi, l'appelant recopie sur la ligne le contenu de l'`AgentOutcome` : `model`, puis `prompt_tokens` et `completion_tokens` quand ils sont supérieurs à zéro. Une ligne d'un agent qui n'a pas appelé le modèle garde ces trois colonnes à nul.

Un agent additionne les compteurs de chaque `LLMResult` reçu pendant son exécution, en comptant `None` pour zéro, et lit le nom du modèle sur le port (`llm.model`).

Tests : `tests/unit/test_journal.py`, et `tests/contrats/test_v0_03_journal.py` pour le contrat. Cas attendus : ligne `ok` avec `ended_at`, ligne `en_cours` pendant le bloc, ligne `erreur` qui survit à l'annulation de l'appelant, exception relancée, base créée sous `CABINET_HOME`, deux répertoires et deux bases, clés étrangères actives à chaque connexion.

## 3. Orchestrateur

```python
# src/cabinet/agents/pipeline.py
class PipelineBusy(Exception): ...

class Pipeline:
    def __init__(self, agents: list[Agent], lock_path: Path): ...
    def run(self, session_factory: Callable[[], Session]) -> dict[str, AgentOutcome | str]: ...

def build_pipeline(settings: Settings) -> Pipeline: ...
```

### 3.1 `Pipeline.run`

1. Prendre le verrou (section 3.2). S'il est tenu, lever `PipelineBusy`.
2. Pour chaque agent, dans l'ordre de la liste :
   1. Ouvrir une session par `session_factory()`.
   2. Exécuter `agent.run(session)` à l'intérieur de `record_run(session, agent.name)`.
   3. En cas de succès, recopier le modèle et les tokens sur la ligne de journal (section 2.3) et ranger l'`AgentOutcome` dans le résultat sous le nom de l'agent.
   4. En cas d'exception, `record_run` a déjà journalisé l'erreur. Ranger le message de l'exception (une chaîne) dans le résultat et passer à l'agent suivant.
   5. Fermer la session dans tous les cas.
3. Retirer le verrou dans un bloc `finally`, y compris après une exception inattendue.
4. Renvoyer le résultat. L'ordre des clés est l'ordre d'exécution.

Une valeur de type `AgentOutcome` signale un agent allé au bout, avec ou sans erreurs d'éléments. Une valeur de type `str` signale un agent qui a levé une exception.

### 3.2 Verrou

Le verrou est le fichier `paths().lock_file`, soit `data/pipeline.lock`.

1. Tenter de le créer de façon atomique (`os.open(chemin, os.O_CREAT | os.O_EXCL | os.O_WRONLY)`) et y écrire le numéro du processus courant.
2. Si le fichier existe déjà, lire son contenu. S'il contient le numéro d'un processus qui n'existe plus (`os.kill(pid, 0)` lève `ProcessLookupError`), le verrou est périmé : le supprimer et reprendre à l'étape 1, une seule fois.
3. Dans tous les autres cas (processus vivant, contenu vide ou illisible), lever `PipelineBusy`.

La reprise d'un verrou périmé n'était pas dans le kit. Sans elle, un redémarrage de la machine pendant un passage bloquerait tous les passages suivants jusqu'à la suppression du fichier à la main.

### 3.3 `build_pipeline`

`build_pipeline` assemble les adaptateurs réels. La liste grandit avec les lots :

| Lot | Agents assemblés, dans l'ordre |
| --- | --- |
| 1 (V1-08) | `IngestionAgent(FolderInbox(paths().inbox))`, `AgendaAgent(IcsCalendarSource(settings.agenda.ics_path))` |
| 2 | puis `TranscriptsAgent()` (V2-01), `TranscriptionAgent(MlxWhisperTranscriber(settings.transcription.model))` (V2-03), `MinutesAgent(llm, settings.llm.context_tokens)` (V2-04), `PreparationAgent(llm)` (V2-08) |
| 3 | puis `TimesheetsAgent(llm, settings.cra.regles, settings.cra.deadline)` (V3-02) |

`llm` est un seul `LocalLLM(settings.llm.base_url, settings.llm.model, settings.llm.timeout_s)`, construit à partir du lot 2 et partagé par les agents. Le verrou est `paths().lock_file`.

Tests : `tests/unit/test_pipeline.py`, avec des agents factices qui notent leur ordre de passage. Cas attendus : ordre respecté, agent en exception journalisé `erreur` et agent suivant exécuté, `PipelineBusy` quand le verrou porte le numéro du processus de test, verrou retiré après un passage normal et après un passage en erreur, verrou périmé repris.

## 4. Rangement d'un fichier lu

Les agents `transcripts`, `transcription` et `timesheets` rangent chaque fichier lu avec succès. La fonction est commune.

```python
# src/cabinet/agents/base.py
def archive_file(item: InboxItem) -> None: ...
```

1. Le dossier cible est `paths().processed / AAAA-MM`, où le mois est celui de `clock.today()`. Le créer s'il manque.
2. Le nom cible est le nom du fichier. Si un fichier de ce nom existe déjà dans le dossier, insérer `_{item.id}` avant l'extension.
3. Déplacer le fichier (`shutil.move`) et écrire dans `item.path` le nouveau chemin, relatif au répertoire de données.
4. Passer `item.status` à `traite`. L'appelant valide la session.

Un fichier à l'état `erreur` ou `ignore` reste dans `entree/`.

## 5. Ingestion

```python
# src/cabinet/adapters/folder_inbox.py
class FolderInbox:
    def __init__(self, root: Path): ...
    def scan(self) -> list[InboxFile]: ...

# src/cabinet/agents/ingestion.py
class IngestionAgent:
    name = "ingestion"
    def __init__(self, inbox: Inbox): ...
    def run(self, session: Session) -> AgentOutcome: ...
```

### 5.1 Nature d'un fichier

La nature se déduit du nom, sans modèle de langage. L'extension et le préfixe se comparent sans tenir compte de la casse.

| Extension | Préfixe du nom | Nature | État de départ |
| --- | --- | --- | --- |
| `.vtt`, `.docx` | tout | `teams_transcript` | `a_rattacher` |
| `.md` | tout | `notes` | `a_rattacher` |
| `.txt` | autre que `app_` | `notes` | `a_rattacher` |
| `.m4a`, `.mp3`, `.wav` | tout | `audio` | `consentement_requis` |
| `.html`, `.txt` | `app_` | `app_capture_text` | `pret` |
| `.png`, `.jpg`, `.jpeg` | `app_` | `app_capture_image` | `pret` |
| tout autre cas | tout | `inconnu` | `ignore` |

Un fichier `.html`, `.png`, `.jpg` ou `.jpeg` sans le préfixe `app_` est de nature `inconnu`.

Un nom qui commence par `AAAA-MM-JJ_` donne `name_date` (expression `^(\d{4})-(\d{2})-(\d{2})_`). Une date impossible, comme `2026-13-40_`, donne `None`. Tout autre nom donne `None`.

### 5.2 `FolderInbox.scan`

1. Si `root` n'existe pas, renvoyer une liste vide.
2. Parcourir les entrées directes de `root`, triées par nom. Ignorer les sous-dossiers, les noms qui commencent par `.` ou par `~$`, et les fichiers modifiés depuis moins de 5 secondes (`clock.now().timestamp() - st_mtime < 5`), qui sont peut-être en cours de copie.
3. Pour chaque fichier restant, calculer le sha256 par blocs de 1 Mio, la nature et `name_date`.
4. Renvoyer un `InboxFile` par fichier, avec le chemin absolu et la taille en octets.

Un fichier qui ne peut pas être lu au moment du calcul d'empreinte (`OSError`, par exemple des droits retirés) est laissé de côté comme un fichier en cours de copie, sans lever d'erreur. Il est repris au passage suivant. Il ne peut pas devenir un élément à l'état `erreur`, faute d'empreinte (décision du 26.11.2026, question Q2 de `revues/vague-1-questions.md`).

### 5.3 `IngestionAgent.run`

1. Appeler `inbox.scan()`.
2. Pour chaque fichier dont le `sha256` n'est dans aucun `inbox_item` : créer l'élément avec sa nature, son état de départ (tableau 5.1), son chemin relatif au répertoire de données et `detected_at = clock.now()`. Valider.
3. Un fichier dont l'empreinte est connue est laissé de côté, quel que soit son nom. Deux fichiers identiques dans le même dossier ne donnent qu'un élément.
4. Renvoyer `AgentOutcome(processed=n)`, où `n` est le nombre d'éléments créés.

L'agent ne lit pas le contenu d'un fichier au-delà du calcul d'empreinte et ne déplace rien.

Tests : `tests/unit/test_ingestion.py`, sur un dossier temporaire. Cas attendus : chaque ligne du tableau 5.1 avec une extension en majuscules, `2026-11-23_point-hebdo.vtt` daté du 23.11.2026 et `notes.md` sans date, fichier caché, fichier `~$brouillon.docx` et fichier modifié à l'instant ignorés, aucun doublon après deux passages, états de départ d'un audio, d'une capture `app_nov.html` et d'un `.zip`.

## 6. Agenda

```python
# src/cabinet/ports/calendar.py
class CalendarError(Exception): ...

# src/cabinet/adapters/ics_calendar.py
class IcsCalendarSource:
    def __init__(self, path: Path): ...
    def list_events(self, start: date, end: date) -> list[EventData]: ...

# src/cabinet/services/series.py
def series_key(title: str) -> str: ...

# src/cabinet/agents/agenda.py
class AgendaAgent:
    name = "agenda"
    def __init__(self, calendar: CalendarSource): ...
    def run(self, session: Session) -> AgentOutcome: ...
```

### 6.1 `IcsCalendarSource.list_events`

1. Si le fichier n'existe pas, lever `CalendarError` : « Fichier d'agenda introuvable : {chemin}. Exporte ton agenda au format ICS et place-le à cet endroit. »
2. Lire le fichier avec `icalendar.Calendar.from_ical(octets)`. Une erreur de lecture lève `CalendarError` : « Fichier d'agenda illisible : {détail}. »
3. Développer les récurrences sur la fenêtre, du début du jour `start` à la fin du jour `end` :

   ```python
   recurring_ical_events.of(calendrier).between(start, end + timedelta(days=1))
   ```

   La forme de cet appel n'a pas été relevée sur une source le 24.11.2026 : la fiche PyPI du paquet ne la donne pas. Elle est à confirmer dans la documentation du paquet à l'implémentation. Le comportement attendu, lui, est fixé : un évènement du jour `end` est rendu, un évènement du lendemain ne l'est pas.

4. Pour chaque occurrence, construire un `EventData` :

   | Champ | Source ICS | Règle |
   | --- | --- | --- |
   | `uid` | `UID` | chaîne |
   | `title` | `SUMMARY` | chaîne, « (sans titre) » si absent |
   | `start`, `end` | `DTSTART`, `DTEND` | converties dans `LOCAL_TZ`. Une heure sans fuseau est prise dans `LOCAL_TZ`. Sans `DTEND`, `end` vaut `start` |
   | `all_day` | type de `DTSTART` | vrai si c'est une date sans heure. `start` vaut alors minuit de ce jour dans `LOCAL_TZ`, et `end` minuit du jour de `DTEND`. Sans `DTEND`, la journée dure un jour (RFC 5545) : `end` vaut minuit du lendemain |
   | `location` | `LOCATION` | chaîne, `None` si absent ou vide |
   | `organizer` | `ORGANIZER` | adresse sans `mailto:`, en minuscules, `None` si absent |
   | `attendees` | `ATTENDEE` | adresses sans `mailto:`, en minuscules, dans l'ordre du fichier, sans doublon |
   | `cancelled` | `STATUS`, `SUMMARY` | vrai si `STATUS` vaut `CANCELLED`, ou si le titre commence par `Annulé` ou `Canceled`, sans tenir compte de la casse |

5. Renvoyer la liste triée par `start` puis par `uid`.

L'adaptateur lit un fichier local. Il ne lit jamais une adresse web et n'écrit jamais dans l'agenda.

### 6.2 `AgendaAgent.run`

1. Fixer la fenêtre : de 30 jours avant `clock.today()` à 14 jours après.
2. Appeler `calendar.list_events`. Une `CalendarError` remonte : le journal l'enregistre et le pipeline passe à l'agent suivant.
3. Pour chaque `EventData`, chercher la ligne `event` de mêmes `uid` et `start`. La créer si elle manque, sinon mettre à jour ses autres champs.
4. Si l'évènement n'est ni annulé ni sur la journée entière :
   1. Calculer `series_key(title)`. Chercher la série de cette clé. La créer si elle manque, avec `label = title` et `followed` à faux.
   2. Chercher la réunion de cet `event_id`. La créer si elle manque, avec le titre, la série et `date = start.date()`. Si elle existe, mettre à jour son titre et sa date, jamais sa série.

Une réunion garde la série de sa création. Si un renommage change la clé de série, les réunions déjà créées restent dans l'ancienne série, et seules les réunions créées après le renommage rejoignent la nouvelle (décision du 26.11.2026, question Q1 de `revues/vague-1-questions.md`).
5. Valider, puis renvoyer `AgentOutcome(processed=n)`, où `n` est le nombre d'évènements créés pendant ce passage.

Ce que l'agent ne fait pas : il ne supprime jamais un évènement, une série ni une réunion. Un évènement retiré du fichier ICS reste en base. Une réunion déjà créée dont l'évènement devient annulé reste en base, et son évènement porte `cancelled` à vrai. Une occurrence déplacée donne une nouvelle ligne `event`, parce que la clé d'unicité inclut `start`.

### 6.3 `series_key`

1. Mettre le titre en minuscules (`casefold`).
2. Retirer les accents (décomposition `NFKD`, puis suppression des caractères combinants).
3. Retirer en tête, autant de fois qu'ils se présentent, les préfixes `re:`, `tr:`, `fw:`, `annule:` et `canceled:`, suivis ou non d'espaces.
4. Remplacer par une espace tout caractère qui n'est ni une lettre ni une espace. Les chiffres et la ponctuation disparaissent.
5. Réduire les suites d'espaces à une seule et retirer celles du début et de la fin.
6. Si le résultat est vide, renvoyer `sans titre`.

| Titre | Clé |
| --- | --- |
| `RE: Point hebdo 12` | `point hebdo` |
| `Point hebdo` | `point hebdo` |
| `Comité été` | `comite ete` |
| `TR: RE: Comité de pilotage (3/5)` | `comite de pilotage` |
| `2026` | `sans titre` |

Tests : `tests/unit/test_agenda.py`, avec des fichiers ICS synthétiques dans `tests/fixtures/agenda/`. Cas attendus : évènement simple avec titre, lieu, organisateur et participants, évènement hebdomadaire et une occurrence par semaine dans la fenêtre, annulation par statut et par titre sans réunion créée, égalités de clés du tableau ci-dessus, aucun doublon après deux passages, fichier absent et erreur au journal, heures relues dans le fuseau `Europe/Zurich`.

## 7. Ce qu'un implémenteur doit garder en tête

- `record_run` valide la session de l'appelant. L'ouvrir en premier, sur une session propre.
- Une exception dans un agent ne doit jamais empêcher le retrait du verrou ni l'exécution de l'agent suivant.
- Un verrou au contenu vide ou illisible est un verrou tenu. Seul un numéro de processus mort le rend périmé.
- L'unicité d'un élément d'entrée est son sha256, pas son nom. Un fichier renommé n'est pas un nouveau fichier.
- `name_date` vient du nom du fichier et seulement du nom. La date de modification ne sert qu'à ignorer un fichier en cours de copie.
- Les adresses de l'agenda se stockent sans `mailto:` et en minuscules. `mailto_link` les reprend telles quelles.
- La clé d'une série ne contient ni chiffre ni ponctuation. Deux réunions numérotées d'une même série ont la même clé, c'est voulu.
- L'agent d'agenda ne supprime rien. Ne pas « nettoyer » les évènements absents du fichier.
