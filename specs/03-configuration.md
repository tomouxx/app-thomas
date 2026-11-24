# Configuration

Répertoire de données, variables d'environnement, réglages, référentiel d'équipe, ligne de commande et fichiers d'outillage du dépôt. Les valeurs par défaut de ce document font foi. Les fichiers de `exemples/` les reproduisent.

## 1. Répertoire de données et chemins

Toutes les données de l'utilisateur vivent sous un seul répertoire, hors du dépôt.

```text
$CABINET_HOME/
  entree/        fichiers déposés par l'utilisateur
  traite/        fichiers lus, rangés par mois : traite/AAAA-MM/
  brouillons/    exports Markdown des documents validés
  config/        reglages.toml, equipe.yaml, agenda.ics
  data/          cabinet.db, pipeline.lock
```

```python
# src/cabinet/config.py
class Paths(BaseModel):
    home: Path
    inbox: Path        # home / "entree"
    processed: Path    # home / "traite"
    drafts: Path       # home / "brouillons"
    config: Path       # home / "config"
    data: Path         # home / "data"

    @property
    def settings_file(self) -> Path: ...   # config / "reglages.toml"
    @property
    def roster_file(self) -> Path: ...     # config / "equipe.yaml"
    @property
    def db_file(self) -> Path: ...         # data / "cabinet.db"
    @property
    def lock_file(self) -> Path: ...       # data / "pipeline.lock"

def paths() -> Paths: ...
```

Algorithme de `paths()` :

1. Lire la variable `CABINET_HOME`. Si elle est définie et non vide, `home` vaut ce chemin, rendu absolu (`Path(...).expanduser().resolve()`).
2. Sinon `home` vaut `Path("~/Cabinet").expanduser()`.
3. Construire les cinq sous-dossiers. Ne rien créer sur le disque.

`paths()` relit la variable à chaque appel. Elle ne garde aucun résultat en mémoire, ce qui permet à un test de changer `CABINET_HOME` entre deux appels.

Qui crée quoi :

| Dossier ou fichier | Créé par |
| --- | --- |
| les cinq dossiers, `config/reglages.toml` | `cabinet init` (section 5.2) |
| `data/` et `data/cabinet.db` | `init_db()`, si absents (`specs/05 §2.1`) |
| `traite/AAAA-MM/` | l'agent qui range un fichier lu |
| `brouillons/` | la validation d'un brouillon ou d'un compte rendu, si absent |
| `config/equipe.yaml`, `config/agenda.ics` | l'utilisateur |

Avant la tâche V1-01, `config.py` n'existe pas : `db/base.py` calcule lui-même `data/cabinet.db` à partir de `CABINET_HOME`. La tâche V1-01 le fait passer par `paths()`. À partir de là, la chaîne `~/Cabinet` n'apparaît dans `src/` que dans `config.py`.

## 2. Variables d'environnement

| Variable | Lue par | Effet |
| --- | --- | --- |
| `CABINET_HOME` | `paths()` | remplace `~/Cabinet`. Les tests la pointent toujours sur un répertoire temporaire |
| `CABINET_ALLOW_REMOTE_LLM` | `assert_loopback` | la valeur exacte `1` lève la garde de bouclage (`specs/04 §2`). Toute autre valeur la laisse en place. Cette variable n'est documentée nulle part dans l'interface |

L'application ne lit aucune autre variable. `CABINET_TICKET`, `CABINET_CONTRATS` et `CABINET_RAPPORT_CONTRATS` appartiennent à la chaîne de développement et ne sont lues que par `tests/contrats/conftest.py` (`docs/REPRISE.md`, section 5).

## 3. Réglages

Fichier `$CABINET_HOME/config/reglages.toml`. Il est facultatif : sans lui, les valeurs par défaut s'appliquent.

```toml
[llm]
base_url = "http://127.0.0.1:8080/v1"
model = "qwen-local"
timeout_s = 900
context_tokens = 64000

[transcription]
model = "mlx-community/whisper-large-v3-mlx"
delete_audio_after_validation = true

[agenda]
ics_path = "config/agenda.ics"

[cra]
# deadline = 2026-11-30

[cra.regles]
R1 = true
R2 = true
R3 = true
R4 = true
tolerance_jours = 0.0

[ui]
port = 8765
```

Ce texte est le contenu exact du fichier écrit par `cabinet init` et de `exemples/reglages.exemple.toml`. Il vit dans `config.py` sous la constante `DEFAULT_SETTINGS_TOML`.

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class LLMSettings(StrictModel):
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "qwen-local"            # alias, voir specs/00 §4.4
    timeout_s: float = 900               # délai d'un appel, voir specs/00 §4.2
    context_tokens: int = 64000          # sert au découpage des sources longues (specs/06 §6)

class TranscriptionSettings(StrictModel):
    model: str = "mlx-community/whisper-large-v3-mlx"
    delete_audio_after_validation: bool = True

class AgendaSettings(StrictModel):
    ics_path: Path = Path("config/agenda.ics")   # relatif au répertoire de données, ou absolu

class RulesSettings(StrictModel):
    R1: bool = True
    R2: bool = True
    R3: bool = True
    R4: bool = True
    tolerance_jours: float = 0.0         # jours manquants tolérés par R1

class CraSettings(StrictModel):
    deadline: date | None = None         # échéance des imputations du mois
    regles: RulesSettings = RulesSettings()

class UISettings(StrictModel):
    port: int = 8765                     # affiché à la page Réglages. `make run` fixe 8765

class Settings(StrictModel):
    llm: LLMSettings = LLMSettings()
    transcription: TranscriptionSettings = TranscriptionSettings()
    agenda: AgendaSettings = AgendaSettings()
    cra: CraSettings = CraSettings()
    ui: UISettings = UISettings()

def load_settings() -> Settings: ...
def set_cra_deadline(day: date | None) -> None: ...
```

### 3.1 `load_settings`

1. Si `paths().settings_file` n'existe pas, partir d'un dictionnaire vide.
2. Sinon le lire avec `tomllib.load`. Une erreur de syntaxe lève `ConfigError` : « reglages.toml est illisible : {détail}. »
3. Valider avec `Settings.model_validate`. Pour la première erreur de la liste pydantic : si son type est `extra_forbidden`, lever `ConfigError` avec « Clé inconnue dans reglages.toml : {clé}. », où la clé est le chemin pointé (`cra.regles.R5`). Sinon lever `ConfigError` avec « Valeur invalide pour {clé} : {message}. »
4. Appeler `assert_loopback(settings.llm.base_url)`. Une adresse distante lève `ConfigError` (`specs/04 §2`).
5. Si `agenda.ics_path` est relatif, le remplacer par `paths().home / ics_path`.
6. Renvoyer l'objet. Aucun cache : chaque appel relit le fichier.

### 3.2 `set_cra_deadline`

C'est la seule écriture de l'application dans `reglages.toml` (`POST /imputations/echeance`, `specs/08 §6.3`). Elle ne réécrit pas le fichier depuis l'objet `Settings` : elle modifie une ligne et laisse toutes les autres à l'identique, commentaires compris.

1. Lire le fichier ligne à ligne, en gardant les fins de ligne. S'il n'existe pas, partir de `DEFAULT_SETTINGS_TOML`.
2. Repérer l'en-tête de section `[cra]` (expression `^\s*\[cra\]\s*(#.*)?$`). La section s'étend jusqu'à la prochaine ligne qui commence par `[`.
3. Dans la section, repérer la ligne active de l'échéance (`^\s*deadline\s*=`). Une ligne commentée ne compte pas.
4. Avec une date : remplacer cette ligne par `deadline = AAAA-MM-JJ`, ou l'insérer juste après l'en-tête si elle manque. Sans section `[cra]`, ajouter à la fin du fichier une ligne vide, `[cra]` et la ligne `deadline`.
5. Avec `None` : supprimer la ligne active si elle existe, sinon ne rien changer.
6. Écrire dans un fichier temporaire du même dossier, puis le renommer sur `reglages.toml` (`os.replace`).

Tests : `tests/unit/test_config.py`. Cas attendus : fichier absent et valeurs par défaut, clé inconnue nommée dans le message, adresse distante refusée, chemin d'agenda relatif résolu, échéance remplacée puis fichier comparé ligne à ligne au fichier d'origine, échéance ajoutée quand la ligne est commentée.

## 4. Référentiel d'équipe

Fichier `$CABINET_HOME/config/equipe.yaml`, écrit par l'utilisateur. Il contient des données nominatives et ne va jamais dans le dépôt. `exemples/equipe.exemple.yaml` en donne la forme avec des personnes fictives.

```yaml
people:
  - name: Camille Exemple
    email: camille.exemple@exemple.test
    fte: 1.0
    expected_codes: [PROJ-A, PROJ-B]
    absences: []
  - name: Sacha Modele
    email: sacha.modele@exemple.test
    fte: 0.8
    expected_codes: [PROJ-A]
    absences:
      - from: 2026-11-09
        to: 2026-11-10
holidays: []
```

```python
# src/cabinet/services/roster.py
class Absence(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    start: date = Field(alias="from")    # premier jour d'absence, inclus
    end: date = Field(alias="to")        # dernier jour d'absence, inclus

class Person(StrictModel):
    name: str
    email: str
    fte: float = Field(default=1.0, gt=0, le=1)   # taux d'activité
    expected_codes: list[str]            # codes projet prévus pour la personne
    absences: list[Absence] = []

class Roster(StrictModel):
    people: list[Person]
    holidays: list[date] = []            # jours fériés, saisis à la main

def person_key(name: str) -> str: ...
def load_roster() -> Roster: ...
```

`person_key` met le nom en minuscules, retire les accents (`unicodedata.normalize("NFKD", …)` puis suppression des caractères combinants) et réduit les espaces. Deux noms désignent la même personne quand leurs clés sont égales. L'ordre des mots compte : « Exemple Camille » ne correspond pas à « Camille Exemple », et l'écran de vérification propose alors la correspondance à la main (`specs/08 §6.2`).

Algorithme de `load_roster` :

1. Si `paths().roster_file` n'existe pas, lever `ConfigError` : « Référentiel d'équipe introuvable : {chemin}. »
2. Lire avec `yaml.safe_load`. Une erreur de syntaxe, un contenu qui n'est pas un dictionnaire ou l'absence de la clé `people` lèvent `ConfigError` : « Référentiel d'équipe invalide : {détail}. »
3. Valider avec `Roster.model_validate`. Une erreur pydantic lève la même `ConfigError`, avec le chemin du champ en défaut.
4. Refuser deux personnes de même `person_key` : « Référentiel d'équipe invalide : {nom} apparaît deux fois. »
5. Refuser une absence dont `end` précède `start`.

Le calcul des jours ouvrés et des jours attendus est dans `specs/07 §1`.

## 5. Ligne de commande

Commande `cabinet`, déclarée dans `pyproject.toml` (`cabinet = "cabinet.cli:app"`). `cli.py` crée `app = typer.Typer(no_args_is_help=True)` et déclare une fonction de rappel vide, pour que les sous-commandes soient toujours exigées (`specs/00 §4.9`).

```python
@app.callback()
def main() -> None:
    """Cabinet, assistant local de réunions et d'imputations."""
```

| Commande | Tâche | Sortie | Codes de sortie |
| --- | --- | --- | --- |
| `cabinet version` | V0-01 | `0.1.0`, lue par `importlib.metadata.version("cabinet")` | 0 |
| `cabinet llm-test` | V0-02 | `OK`, ou le message d'erreur sur la sortie d'erreur | 0, ou 1 en cas d'échec |
| `cabinet init` | V1-01 | le chemin du répertoire de données et la liste de ce qui a été créé | 0 |
| `cabinet run` | V1-08 | une ligne par agent | 0 sans erreur, 1 avec au moins une erreur, 2 si un passage est déjà en cours |

### 5.1 `cabinet llm-test`

1. Construire `LocalLLM` avec `base_url`, `model` et `timeout_s`. Avant la tâche V1-01, ces valeurs sont les valeurs par défaut de la section 3, écrites dans `cli.py`. À partir de V1-01 elles viennent de `load_settings()`.
2. Appeler `complete` avec un message utilisateur « Réponds par l'objet JSON {"ok": true}. » et le schéma `Ping` (`class Ping(BaseModel): ok: bool`).
3. Si `parsed.ok` est vrai, afficher `OK` et sortir avec 0.
4. Sur `ConfigError` ou `LLMError`, ou si `ok` est faux, afficher le message sur la sortie d'erreur et sortir avec 1.

Cette commande parle à un vrai serveur. Aucun test automatique ne la lance contre le réseau : elle se vérifie à la recette du lot 0 (`specs/09 §6`).

### 5.2 `cabinet init`

1. Créer `home`, `entree/`, `traite/`, `brouillons/`, `config/` et `data/` (`mkdir(parents=True, exist_ok=True)`).
2. Si `config/reglages.toml` n'existe pas, y écrire `DEFAULT_SETTINGS_TOML`. S'il existe, ne pas y toucher.
3. Afficher le chemin de `home`, puis une ligne par dossier ou fichier créé pendant cet appel.

La commande ne remplace jamais un fichier existant. Elle ne crée ni `equipe.yaml` ni `agenda.ics`.

### 5.3 `cabinet run`

1. Charger les réglages, appeler `init_db()`, construire le pipeline par `build_pipeline(settings)` (`specs/05 §3.3`).
2. Lancer `pipeline.run(get_session)`. Sur `PipelineBusy`, afficher « Un traitement est déjà en cours. » et sortir avec 2.
3. Afficher une ligne par agent, dans l'ordre : `ingestion : 3 traité(s), 0 erreur(s)`, ou `agenda : échec : {message}` quand l'agent a levé une exception.
4. Sortir avec 1 si un agent a levé une exception ou compte au moins une erreur. Sinon sortir avec 0.

## 6. Makefile, `pyproject.toml` et `.gitignore`

```make
.PHONY: install check run

install:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

check:
	.venv/bin/ruff check .
	.venv/bin/pytest -q

run:
	.venv/bin/uvicorn cabinet.web.app:create_app --factory --host 127.0.0.1 --port 8765
```

`make check` est la seule commande de vérification. `make run` lie le serveur à `127.0.0.1` et à aucune autre interface. La chaîne `0.0.0.0` n'apparaît nulle part dans le `Makefile`.

```toml
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "cabinet"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.165.0",
    "uvicorn>=0.54.0",
    "jinja2>=3.1.6",
    "python-multipart>=0.0.34",
    "sqlalchemy>=2.1.9,<3",
    "pydantic>=2.14.4",
    "httpx>=0.27,<0.29",
    "typer>=0.29.2",
    "icalendar>=7.3.2,<8",
    "recurring-ical-events>=3.8.2",
    "python-docx>=1.2.0",
    "pyyaml>=6.0.4",
    "beautifulsoup4>=4.15.0",
]

[project.optional-dependencies]
dev = ["pytest>=9.2.0", "ruff>=0.17.6"]
audio = ["mlx-whisper>=0.4.3"]          # ajoutée par la tâche V2-03

[project.scripts]
cabinet = "cabinet.cli:app"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
cabinet = ["web/templates/*.html", "web/static/*"]
```

Les sections `[tool.ruff]` et `[tool.pytest.ini_options]` sont dans `specs/01 §5`. Les versions viennent du relevé de `specs/00 §4.11`. La version de `setuptools` n'a pas été relevée et n'est pas bornée.

`.gitignore` contient au moins : `.venv/`, `__pycache__/`, `*.db`, `.pytest_cache/`, `.ruff_cache/`, `*.egg-info/`.

## 7. Ce qu'un implémenteur doit garder en tête

- `paths()` et `load_settings()` ne gardent rien en mémoire. Ne pas les appeler au chargement d'un module, mais au moment de s'en servir.
- Une clé inconnue de `reglages.toml` ou de `equipe.yaml` est une erreur qui nomme la clé. Ne jamais l'ignorer en silence.
- `reglages.toml` appartient à l'utilisateur. La seule écriture permise est celle de la ligne `deadline`, par `set_cra_deadline`, sans toucher au reste.
- `ui.port` ne change pas le port de `make run`. Ne pas brancher l'un sur l'autre.
- `Absence` se lit avec les clés `from` et `to` du fichier, et s'utilise en Python avec `start` et `end`.
- Les noms de personnes se comparent par `person_key`, jamais par égalité de chaînes.
- `cabinet init` et `set_cra_deadline` ne remplacent jamais un contenu que l'utilisateur a écrit.
