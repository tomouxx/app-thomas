# Spécification de Cabinet, version 1

Version 1.0 du 23.11.2026. Rédigée par l'architecte (Claude, session de cadrage avec Thomas). « Cabinet » est un nom de travail.

Ce document fixe l'architecture. L'exécutant ne la modifie pas. Toute question d'architecture remonte à l'architecte par une note dans `tickets/notes/`.

## 1. But

Cabinet est une application locale, pour un seul utilisateur, qui prépare son travail et ne l'envoie jamais à sa place. La version 1 couvre deux besoins, dans cet ordre de priorité.

1. Préparer les réunions et rédiger leurs comptes rendus.
2. Contrôler les imputations mensuelles de l'équipe et préparer les relances.

Tout tourne sur un seul Mac (Apple Silicon, 64 Go de RAM ou plus), avec un modèle de langage servi en local. Aucune donnée ne sort de la machine.

## 2. Périmètre

### 2.1 Dans la version 1

- L'application lit un dossier d'entrée où l'utilisateur dépose transcripts Teams, notes, fichiers audio et captures de l'outil d'imputation.
- Elle lit l'agenda depuis un fichier ICS.
- Elle transcrit l'audio en local.
- Elle produit un compte rendu structuré (résumé, décisions, actions, points ouverts) dont chaque décision et chaque action renvoie à un extrait de la source.
- Elle suit les actions ouvertes d'une réunion à la suivante.
- Elle produit une fiche de préparation pour les réunions à venir des séries que l'utilisateur a choisi de suivre.
- Elle contrôle une capture d'imputations relue par l'utilisateur contre un référentiel d'équipe et produit un brouillon de relance par personne.
- Une page web locale sert à tout relire, corriger, valider ou rejeter.

### 2.2 Hors de la version 1

- Aucun envoi de message, sous aucune forme.
- Aucune lecture de la messagerie ni de l'agenda par API. Les interfaces sont prêtes pour un adaptateur Microsoft Graph en version 2.
- Aucun pilotage de navigateur. La saisie dans les outils internes reste manuelle ou passe par les outils existants de l'utilisateur.
- Pas de brief du jour (version 1.1), pas de tri des mails (version 2).
- Pas d'enregistrement audio par l'application. Elle transcrit un fichier déposé.
- Pas de multi-utilisateur, pas d'authentification. Le serveur n'écoute que sur 127.0.0.1.

## 3. Principes non négociables

Ces règles priment sur tout ticket. Un ticket qui semble les contredire est mal lu ou mal écrit, et l'exécutant s'arrête.

1. Brouillons seulement. Le code ne contient aucune fonction d'envoi (pas de SMTP, pas d'appel Graph d'envoi, pas d'automatisation de la messagerie).
2. Local seulement. L'adresse du modèle doit être une adresse de bouclage. Le chargement de la configuration refuse toute autre adresse.
3. Consentement avant audio. Un fichier audio n'est transcrit que si l'utilisateur a coché, pour ce fichier, qu'il a l'accord des participants. Le contrôle est fait côté serveur, deux fois (à la page Entrée et dans l'agent de transcription).
4. Pas de donnée réelle dans le dépôt. Les jeux de test sont synthétiques, avec des noms et des adresses fictifs. Les données réelles vivent dans le répertoire de données, hors du dépôt.
5. Tout est journalisé. Chaque exécution d'agent laisse une ligne dans le journal, y compris en cas d'erreur.
6. Pas de modèle de langage là où une règle suffit. Le découpage des fichiers, le rattachement aux réunions, les règles de contrôle et les relances sont déterministes. Le modèle sert à extraire un compte rendu, proposer un ordre du jour et lire une capture d'écran.
7. Le texte lu est une donnée. Un transcript, une note ou une capture ne sont jamais traités comme des instructions, et les prompts le disent explicitement au modèle.

## 4. Architecture

### 4.1 Vue d'ensemble

```
dossier d'entrée ─┐
fichier ICS ──────┼─> agents (pipeline) ──> base SQLite ──> page web locale
modèle local ─────┘         │
transcription locale ───────┘
```

L'application suit le schéma ports et adaptateurs. Les agents ne connaissent que des interfaces (les ports). Les fichiers, le format ICS, le serveur du modèle et la bibliothèque de transcription sont des adaptateurs remplaçables. C'est ce qui permettra de brancher Microsoft Graph plus tard sans toucher aux agents.

### 4.2 Arborescence du dépôt

```
pyproject.toml
Makefile
src/cabinet/
  config.py            réglages et chemins
  cli.py               commandes: version, init, run, llm-test
  db/base.py           moteur, session, Base
  db/models.py         tables
  ports/               interfaces (inbox, calendar, transcriber, llm)
  adapters/            folder_inbox, ics_calendar, mlx_whisper_transcriber
  llm/client.py        client HTTP du modèle local
  llm/types.py
  llm/prompts/         un module par prompt
  readers/             teams_vtt, teams_docx, notes, app_capture
  services/            series, matching, quotes, chunking, drafts,
                       minutes_render, prep_render, workdays, roster,
                       rules, reminders
  agents/              base, pipeline, journal, ingestion, agenda,
                       transcripts, transcription, minutes, preparation,
                       timesheets
  web/app.py           create_app()
  web/routes/          une route par page
  web/templates/
  web/static/          app.css, app.js
tests/
  contrats/            écrits par le relecteur, interdits à l'exécutant
  unit/                écrits par l'exécutant
  fixtures/            données synthétiques
exemples/              fichiers de configuration d'exemple
```

### 4.3 Répertoire de données

Le répertoire de données est `~/Cabinet` par défaut. La variable d'environnement `CABINET_HOME` le remplace. Les tests la pointent toujours sur un répertoire temporaire.

```
$CABINET_HOME/
  entree/        fichiers déposés par l'utilisateur
  traite/        fichiers traités, rangés par AAAA-MM
  brouillons/    exports Markdown des documents validés
  config/        reglages.toml, equipe.yaml, agenda.ics
  data/          cabinet.db, pipeline.lock
```

Seul `config.py` connaît ces chemins. Aucun autre module ne construit un chemin vers le répertoire de données.

## 5. Ports et adaptateurs

### 5.1 Interfaces

```python
class Inbox(Protocol):
    def scan(self) -> list[InboxFile]: ...

class CalendarSource(Protocol):
    def list_events(self, start: date, end: date) -> list[EventData]: ...

class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, language: str = "fr") -> TranscriptData: ...

class LLM(Protocol):
    def complete(
        self,
        messages: list[Message],
        schema: type[BaseModel] | None = None,
        images: list[Path] | None = None,
    ) -> LLMResult: ...

class Agent(Protocol):
    name: str
    def run(self, session: Session) -> AgentOutcome: ...
```

Les types de données (`InboxFile`, `EventData`, `TranscriptData`, `Message`, `LLMResult`, `AgentOutcome`) sont des modèles pydantic définis dans le module du port correspondant. Leurs champs sont donnés dans les tickets.

### 5.2 Nature d'un fichier déposé

La nature se déduit du nom, sans modèle de langage.

| Extension | Préfixe du nom | Nature |
| --- | --- | --- |
| .vtt, .docx | tout | `teams_transcript` |
| .md, .txt | autre que `app_` | `notes` |
| .m4a, .mp3, .wav | tout | `audio` |
| .html, .txt | `app_` | `app_capture_text` |
| .png, .jpg, .jpeg | `app_` | `app_capture_image` |
| autre | tout | `inconnu` |

Un nom de la forme `AAAA-MM-JJ_titre.ext` donne la date de la réunion. Sinon la date de modification du fichier sert de repli.

### 5.3 Adaptateurs de la version 1

- `FolderInbox` lit le dossier `entree/`.
- `IcsCalendarSource` lit le fichier ICS désigné dans les réglages.
- `MlxWhisperTranscriber` appelle la bibliothèque `mlx-whisper`, importée seulement au moment de transcrire.
- `LocalLLM` parle à un serveur compatible avec l'API de complétion de conversation d'OpenAI, à une adresse de bouclage.

## 6. Modèle de données

Base SQLite, SQLAlchemy 2, clés étrangères activées. Les tables sont créées au démarrage. Il n'y a pas de migration en version 1.

| Table | Champs principaux |
| --- | --- |
| `agent_run` | id, agent, target_ref, status (en_cours, ok, erreur), started_at, ended_at, model, prompt_tokens, completion_tokens, error |
| `inbox_item` | id, path, sha256 (unique), kind, status, name_date, meeting_id, consent_confirmed, consent_at, error, detected_at |
| `event` | id, uid, title, start, end, all_day, location, organizer, attendees (JSON), cancelled, unique (uid, start) |
| `series` | id, key (unique), label, followed, client |
| `meeting` | id, event_id (nullable), series_id, title, date |
| `transcript` | id, meeting_id, inbox_item_id, source (teams, audio, notes), text, segments (JSON) |
| `prep_note` | id, meeting_id, content_md, status (brouillon, valide), created_at |
| `minutes` | id, meeting_id, version, content_md, summary, open_points (JSON), next_meeting, status (brouillon, valide), created_at, unique (meeting_id, version) |
| `decision` | id, meeting_id, minutes_id, text, quote, verified |
| `action_item` | id, meeting_id, minutes_id, description, owner, due_date, quote, verified, status (ouvert, fait) |
| `timesheet_capture` | id, inbox_item_id, period (AAAA-MM), source (texte, vision), verified, verified_at, checked_at |
| `timesheet_line` | id, capture_id, person, project_code, days |
| `anomaly` | id, capture_id, person, rule_id, detail, severity (bloquant, a_verifier) |
| `draft` | id, kind (envoi_cr, relance_cra), recipients (JSON), subject, body, status (brouillon, valide, rejete, envoye_a_la_main), meeting_id, capture_id, created_at |

États d'un `inbox_item` : `nouveau`, `a_rattacher`, `consentement_requis`, `pret`, `traite`, `erreur`, `ignore`.

La suppression d'une réunion supprime ses transcripts, fiches, comptes rendus, décisions et actions.

## 7. Agents

Le pipeline exécute les agents dans l'ordre ci-dessous. L'échec d'un agent n'arrête pas les suivants. Un verrou fichier empêche deux exécutions simultanées. Le pipeline se lance par la commande `cabinet run` ou par le bouton « Traiter maintenant ».

| Ordre | Agent | Rôle | Modèle de langage |
| --- | --- | --- | --- |
| 1 | ingestion | Repère les nouveaux fichiers, calcule leur empreinte, fixe leur nature et leur état | non |
| 2 | agenda | Met à jour les évènements, les séries et les réunions depuis le fichier ICS | non |
| 3 | transcripts | Lit les transcripts Teams et les notes rattachés à une réunion | non |
| 4 | transcription | Transcrit les fichiers audio dont le consentement est confirmé | non (reconnaissance vocale locale) |
| 5 | minutes | Extrait le compte rendu structuré et vérifie les extraits | oui |
| 6 | preparation | Rédige la fiche des réunions suivies des 7 prochains jours | oui, pour l'ordre du jour seulement |
| 7 | timesheets | Lit les captures d'imputation, applique les règles aux captures relues, prépare les relances | oui, pour les captures d'écran seulement |

### 7.1 Compte rendu

Le modèle renvoie un objet JSON validé par pydantic.

```python
class DecisionOut(BaseModel):
    text: str
    quote: str            # extrait mot pour mot de la source, 200 caractères au plus

class ActionOut(BaseModel):
    description: str
    owner: str | None     # seulement si un nom est dit dans la source
    due_date: date | None # seulement si une date est dite dans la source
    quote: str

class MinutesOut(BaseModel):
    summary: str          # 120 mots au plus
    decisions: list[DecisionOut]
    actions: list[ActionOut]
    open_points: list[str]
    next_meeting: str | None
```

Chaque `quote` est ensuite cherché dans la source par un contrôle déterministe. Un élément dont l'extrait est introuvable est marqué « à vérifier ». Un compte rendu ne peut pas être validé tant qu'il reste des éléments à vérifier que l'utilisateur n'a ni confirmés ni supprimés.

Le rendu Markdown du compte rendu est fait par un gabarit, pas par le modèle.

### 7.2 Fiche de préparation

La fiche assemble sans modèle l'en-tête de la réunion, le résumé du dernier compte rendu validé de la série, les actions ouvertes de la série, les décisions des trois dernières réunions et les notes déposées pour la réunion à venir. Le modèle ne rédige que la section « Ordre du jour proposé », en 3 à 6 points. Sans historique, la fiche le dit et le modèle n'est pas appelé.

La valeur de la fiche grandit avec l'historique. La première fiche d'une série est presque vide et c'est attendu.

### 7.3 Contrôle des imputations

L'outil d'imputation n'offre pas d'export connu. L'utilisateur dépose une capture de l'écran, dans l'un de ces trois formats, du plus fiable au moins fiable.

1. La page enregistrée en HTML.
2. Le tableau copié dans un fichier texte.
3. Une capture d'écran, lue par le modèle si le serveur local accepte les images.

Quelle que soit la source, l'utilisateur relit les lignes extraites sur un écran de vérification avant tout contrôle. Les règles ne s'appliquent qu'à une capture relue.

Règles de la version 1, toutes paramétrables et à confirmer par l'utilisateur avant la vague 3.

| Règle | Anomalie | Gravité |
| --- | --- | --- |
| R1 | Le total saisi par la personne est inférieur aux jours attendus du mois | bloquant |
| R2 | Une ligne porte un code projet non prévu pour la personne | à vérifier |
| R3 | Une personne du référentiel n'apparaît pas dans la capture | bloquant |
| R4 | Le total saisi dépasse les jours attendus du mois | à vérifier |

Les jours attendus valent les jours ouvrés du mois (lundi à vendredi), moins les jours fériés et les absences du référentiel, multipliés par le taux d'activité.

Les relances sont produites par un gabarit, une par personne, sans modèle.

## 8. Interface web

Pages servies par FastAPI et Jinja2, formulaires HTML simples avec redirection après envoi, sans framework JavaScript. Aucune ressource externe (pas de CDN, pas de police distante). Textes en français.

| Page | Contenu |
| --- | --- |
| Tableau de bord | Réunions des 7 prochains jours, compteurs, échéance des imputations, bouton « Traiter maintenant » |
| Entrée | Fichiers déposés, rattachement à une réunion, case de consentement pour l'audio |
| Réunions | Liste, séries suivies, détail d'une réunion (sources, fiche, compte rendu) |
| Actions | Actions ouvertes, filtres, clôture |
| Imputations | Captures, écran de vérification, anomalies |
| Brouillons | Relire, modifier, valider, rejeter, copier, ouvrir dans la messagerie |
| Journal | 100 dernières exécutions d'agent |
| Réglages | Lecture des réglages, date d'échéance des imputations |

Couleurs de base : fond `#FFFAF0`, texte `#0F0E2B`, accent `#1F24E9`. Police du système.

« Ouvrir dans la messagerie » est un lien `mailto:` qui ouvre un message prérempli dans le client de l'utilisateur. C'est l'utilisateur qui envoie.

## 9. Configuration

Fichier `$CABINET_HOME/config/reglages.toml`.

```toml
[llm]
base_url = "http://127.0.0.1:8080/v1"
model = "qwen-local"
timeout_s = 300
context_tokens = 64000

[transcription]
model = "mlx-community/whisper-large-v3-mlx"
delete_audio_after_validation = true

[agenda]
ics_path = "config/agenda.ics"

[cra]
deadline = 2026-11-30

[cra.regles]
R1 = true
R2 = true
R3 = true
R4 = true
tolerance_jours = 0.0

[ui]
port = 8765
```

Une clé inconnue provoque une erreur explicite au chargement. La variable `CABINET_ALLOW_REMOTE_LLM=1` est le seul moyen de lever la garde de bouclage, et elle n'est pas documentée dans l'interface.

## 10. Données et sécurité

- Le serveur web écoute sur 127.0.0.1 uniquement.
- Les fichiers audio sont supprimés après validation du compte rendu si le réglage est actif (c'est le cas par défaut).
- Le journal garde l'agent, la cible, la durée, le modèle et les compteurs de tokens. Il ne garde pas le texte des prompts.
- Le chiffrement du disque de la machine est supposé actif. L'application ne chiffre rien elle-même.
- Pendant le développement, le relecteur est un modèle distant. Il ne doit jamais lire le répertoire de données. C'est la raison de la règle sur les jeux de test synthétiques.

## 11. Dépendances autorisées

Exécution : fastapi, uvicorn, jinja2, python-multipart, sqlalchemy (version 2), pydantic (version 2), httpx, typer, icalendar, recurring-ical-events, python-docx, pyyaml, beautifulsoup4. En option (`audio`) : mlx-whisper.

Développement : pytest, ruff.

Toute autre dépendance demande l'accord de l'architecte. L'exécutant ne l'ajoute pas de lui-même.

## 12. Conventions de code

- Python 3.12, disposition `src/`, annotations de type partout.
- Identifiants en anglais, textes d'interface, messages d'erreur et docstrings en français.
- Fonctions courtes. Une fonction qui dépasse 40 lignes est découpée.
- Pas d'état global en dehors de la configuration. Les dépendances (session, ports) sont passées en argument.
- Les tests n'appellent jamais le réseau ni un vrai modèle. Ils utilisent `httpx.MockTransport` et des doublures des ports.
- `make check` (ruff puis pytest) doit passer avant chaque commit.

## 13. Hypothèses et limites connues

Ces points sont des hypothèses de l'architecte, pas des faits vérifiés. Ils sont suivis dans `DECISIONS.md`.

- Le harnais de développement (Claude Code relié à Qwen par une passerelle) n'a pas été éprouvé. La vague 0 sert à le vérifier.
- L'agenda peut être exporté ou publié au format ICS depuis la messagerie de l'utilisateur.
- La structure des transcripts Teams au format .docx est supposée (une ligne orateur et horodatage, puis le texte). Le lecteur a un repli en texte brut.
- La structure des écrans de l'outil d'imputation est inconnue. La vague 3 est bloquée tant qu'un échantillon anonymisé n'est pas fourni.
- La lecture d'images dépend du serveur local choisi. Sans elle, le ticket V3-03 est abandonné sans autre conséquence.
- La transcription locale n'attribue pas les paroles aux orateurs. Le compte rendu ne dit pas qui a dit quoi.
- L'enregistrement d'une réunion suppose l'accord des participants. Ce point juridique est à faire valider par l'utilisateur auprès des personnes compétentes avant tout usage réel. La case de consentement est une garde technique, pas une validation juridique.
