# Vision, périmètre et décisions

Document de cadrage de Cabinet, version 1. Les autres documents du dossier `specs/` détaillent chaque partie. Lire celui-ci en premier.

Cette suite remplace `SPEC.md`, `DECISIONS.md` et `PLAN_VAGUES.md` du kit du 23.11.2026. Ces trois fichiers ne contiennent plus qu'une table de correspondance vers les sections d'ici. « Cabinet » est un nom de travail.

| Document | Contenu |
| --- | --- |
| `00-vision-et-decisions.md` | But, contraintes, hors périmètre, décisions vérifiées, principes, glossaire, sources |
| `01-architecture.md` | Composants, arborescence, modèle d'exécution, choix fixés, table des erreurs |
| `02-modele-de-donnees.md` | Tables, états, modèles d'échange |
| `03-configuration.md` | Répertoire de données, réglages, référentiel d'équipe, ligne de commande |
| `04-modele-de-langage.md` | Client du modèle local, garde de bouclage, règles des prompts |
| `05-pipeline-et-socle.md` | Agents, journal, orchestrateur, ingestion, agenda |
| `06-reunions.md` | Sources, transcription, compte rendu, extraits, actions, fiche de préparation |
| `07-imputations.md` | Captures, vérification, règles, relances |
| `08-interface-web.md` | Pages, routes, formulaires, brouillons |
| `09-tests-et-recette.md` | Principes de test, doublures, scénarios, recette |
| `10-plan-implementation.md` | Lots, tâches, critères de fin, dépendances |

## 1. Ce que fait l'application

Cabinet lit les fichiers qu'un utilisateur dépose dans un dossier (transcripts Teams, notes, enregistrements audio, captures de l'outil d'imputation) et son agenda au format ICS, puis prépare des comptes rendus, des fiches de préparation et des relances d'imputation, que l'utilisateur relit et valide dans une page web locale. Elle n'envoie jamais rien.

La version 1 couvre deux besoins, dans cet ordre de priorité : préparer les réunions et rédiger leurs comptes rendus, puis contrôler les imputations mensuelles de l'équipe et préparer les relances.

Chaîne d'étapes du pipeline, dans l'ordre d'exécution :

| Ordre | Agent | Entrée | Sortie (validée) | Moteur par défaut |
| --- | --- | --- | --- | --- |
| 1 | `ingestion` | fichiers de `entree/` | `inbox_item` avec empreinte, nature et état de départ | règles sur le nom du fichier, sha256 |
| 2 | `agenda` | `config/agenda.ics` | `event`, `series`, `meeting` | `icalendar` et `recurring-ical-events` |
| 3 | `transcripts` | éléments `pret` de nature transcript ou notes | `transcript` | lecteurs WebVTT, `python-docx`, texte |
| 4 | `transcription` | éléments audio `pret` au consentement confirmé | `transcript` de source `audio` | `mlx-whisper`, modèle `mlx-community/whisper-large-v3-mlx` |
| 5 | `minutes` | transcripts d'une réunion sans compte rendu | `minutes`, `decision`, `action_item` | modèle local (schéma `MinutesOut`), puis contrôle déterministe des extraits |
| 6 | `preparation` | réunions suivies des 7 prochains jours | `prep_note` | assemblage par gabarit. Modèle local pour l'ordre du jour seulement |
| 7 | `timesheets` | captures `app_`, puis captures relues | `timesheet_capture`, `timesheet_line`, `anomaly`, `draft` | `beautifulsoup4` ou texte, modèle local pour une image, règles R1 à R4, gabarit de relance |

Quatre gestes de l'utilisateur séparent ces étapes, et aucun agent ne les fait à sa place.

| Geste | Page | Ce qu'il débloque |
| --- | --- | --- |
| Rattacher un fichier à une réunion, et confirmer le consentement pour un audio | Entrée | la lecture du fichier (agents 3 et 4) |
| Confirmer ou supprimer les éléments « à vérifier », puis valider le compte rendu | Réunion | l'export, le brouillon d'envoi, le suivi des actions, la suppression de l'audio |
| Relire les lignes d'une capture | Imputations | le passage des règles et les relances |
| Valider un brouillon | Brouillons | le lien `mailto:` et le bouton de copie |

Chaque sortie du modèle de langage est validée par un schéma pydantic (`specs/02 §3`). Chaque exécution d'agent laisse une ligne dans le journal, y compris en erreur. Le pipeline se relance sans risque : aucun agent ne retraite un élément déjà traité. Une régénération de compte rendu crée une nouvelle version et garde les précédentes.

L'interface est une page web servie sur `127.0.0.1:8765`. Elle permet de tout relire, corriger, valider ou rejeter.

## 2. Contraintes retenues

### 2.1 Décisions de Thomas (23.11.2026)

| Sujet | Décision |
| --- | --- |
| Priorités | 1. Préparation et comptes rendus de réunion. 2. Administratif récurrent. 3. Brief du jour. 4. Tri des mails |
| Périmètre de la version 1 | Les priorités 1 et 2, confirmé le 24.11.2026 |
| Autonomie des agents | Brouillons que Thomas valide. Aucun envoi |
| Sources des comptes rendus | Audio transcrit en local, transcript Teams déposé à la main, notes tapées |
| Première tâche administrative | Contrôle des imputations et relances |
| Forme des imputations aujourd'hui | Lecture des écrans de l'outil, sans export |
| Interface | Page web locale |
| Machine | Mac Apple Silicon, 64 Go de RAM ou plus |
| Relecture des vagues | Claude Code en local sur un modèle Claude |

### 2.2 Contraintes techniques

| Sujet | Contrainte |
| --- | --- |
| Matériel | Un seul Mac Apple Silicon. Machine relevée le 24.11.2026 : Apple M5 Pro, 64 Go de RAM, macOS 26.6.2 |
| Réseau | Aucune donnée ne sort de la machine. Le serveur web et le serveur du modèle n'écoutent que sur l'adresse de bouclage |
| Utilisateur | Un seul. Ni compte ni authentification |
| Langue | Interface, messages d'erreur, prompts et documents produits en français. Identifiants de code en anglais |
| Modèle de langage | Servi en local par un serveur compatible avec l'API de complétion de conversation d'OpenAI. Modèle de référence : Qwen3.8 27B, conversion MLX 6 bits, 22,8 Go |
| Transcription | En local, par `mlx-whisper`, installé en option |
| Stockage | Un fichier SQLite et des dossiers, sous un seul répertoire de données hors du dépôt |
| Langage | Python 3.12 ou plus, disposition `src/`, `pip` dans un environnement virtuel `.venv` |
| Dépendances | Liste fermée (section 4.11). Tout ajout demande l'accord de l'architecte |
| Données de développement | Jeux synthétiques seulement, noms fictifs, domaine `exemple.test`. Le relecteur est un modèle distant et ne lit jamais le répertoire de données |
| Disque | Le chiffrement du disque de la machine est supposé actif. L'application ne chiffre rien elle-même |

## 3. Hors périmètre de la version 1

| Exclusion | Ce qui la remplace |
| --- | --- |
| Tout envoi de message | Un brouillon validé donne un lien `mailto:` et un bouton de copie. L'utilisateur envoie depuis sa messagerie |
| Lecture de la messagerie ou de l'agenda par API | Un fichier ICS exporté. Les ports `Inbox` et `CalendarSource` sont prêts pour un adaptateur Microsoft Graph en version 2 |
| Pilotage de navigateur | La saisie dans les outils internes reste manuelle |
| Enregistrement audio par l'application | Elle transcrit un fichier déposé. Enregistrer reste un geste conscient de l'utilisateur |
| Attribution des paroles aux orateurs dans un audio | Le compte rendu d'un audio ne dit pas qui a dit quoi |
| Dépôt de fichier par le navigateur | L'utilisateur copie ses fichiers dans `entree/` |
| Modification des réglages dans l'interface | La page Réglages est en lecture seule. Seule l'échéance des imputations se modifie depuis l'interface |
| Planification automatique et surveillance continue du dossier | Le bouton « Traiter maintenant » ou la commande `cabinet run` |
| Migrations de schéma | Les tables sont créées au démarrage |
| Plusieurs utilisateurs, authentification | Écoute sur `127.0.0.1` seulement |
| Calcul des jours fériés, lecture d'un outil de congés | Jours fériés et absences saisis dans `equipe.yaml` |
| Export Word des comptes rendus | Export Markdown dans `brouillons/` |

Prévu après la version 1, non spécifié ici : brief du jour (version 1.1), adaptateurs Microsoft Graph, tri des mails et brouillons de réponse (version 2), export Word au gabarit de l'entreprise, préparation de la saisie du prévisionnel d'occupation. Ce cadrage se fera après quelques semaines d'usage de la version 1.

## 4. Décisions prises après vérification

Ces points corrigent ou précisent le kit du 23.11.2026. Les mesures ont été faites le 24.11.2026 sur la machine de Thomas, avec des données synthétiques. Les sources sont en section 7.

### 4.1 La chaîne de développement n'est pas encore éprouvée

Le kit pose comme hypothèse que Claude Code, relié à Qwen par LiteLLM, termine trois tickets simples sans aide. La vague 0 a été lancée trois fois le 24.11.2026 et n'a rendu aucun verdict.

| Session | Durée | Ce qui s'est passé |
| --- | --- | --- |
| 12:23 à 12:45 | 22 min | Le modèle a lu les scripts de la chaîne puis a dépensé environ 12 000 tokens de raisonnement sur un seul tour, sans écrire de fichier. Arrêt par le relecteur pour régler la chaîne |
| 12:50 à 13:14 | 24 min | Lecture du ticket, de la spécification et des tests de contrat, un tour de raisonnement de 10 minutes, puis 7 fichiers écrits en 2 min 16 s. La machine a redémarré avant le premier `make check` |
| 15:59 à 16:09 | 10 min | Arrêt demandé par Thomas pour reprendre les spécifications |

Ce qui est établi : les appels d'outils de Qwen arrivent corrects dans Claude Code à condition de régler la passerelle avec `use_chat_completions_url_for_anthropic_messages: true` et le fournisseur `lm_studio/`. Sans ce réglage, LiteLLM 1.108.5 passe par l'API « responses » et les arguments des appels arrivent vides.

Conséquences :

- L'hypothèse reste ouverte. Le lot 0 du plan (`specs/10`) garde son rôle de mesure, avec le même seuil.
- Les réglages de la chaîne vivent dans `docs/CHAINE.md` et `docs/REPRISE.md`. Cette suite ne les reformule pas.

### 4.2 Le modèle raisonne à chaque appel, et ce n'est pas réglable

Qwen3.8 raisonne par défaut au niveau le plus élevé de son gabarit de conversation. LM Studio 0.4.15 ne transmet à ce modèle ni `reasoning_effort` ni `chat_template_kwargs`, et son API native répond que le modèle n'expose pas de réglage de raisonnement.

Mesures sur le port 8080, sans la passerelle :

| Appel | Tokens d'entrée | Tokens de sortie | Dont raisonnement | Durée |
| --- | --- | --- | --- | --- |
| Renvoyer `{"ok": true}` | 74 | 51 | 43 | 4,6 s |
| Compte rendu d'une source de 5 lignes, schéma `MinutesOut` dans le prompt | 457 | 968 | 666 | 77,5 s |

Le débit observé est d'environ 12 tokens par seconde. Le raisonnement arrive dans un champ séparé, `message.reasoning_content`. Le champ `message.content` ne contient que la réponse, précédée de deux retours à la ligne.

Conséquences :

- `LocalLLM` ne lit que `message.content`, retire les espaces autour, et ignore tout autre champ (`specs/04 §3`).
- Le délai par défaut d'un appel passe de 300 à 900 secondes (`llm.timeout_s`, `specs/03 §3`). Aucune mesure n'a été faite sur une source longue. Ce délai est à revoir après l'essai manuel du lot 2.
- Un passage du pipeline peut durer plusieurs minutes par réunion. `POST /traiter` reste synchrone en version 1, et la commande `cabinet run` est la voie conseillée pour un traitement long (`specs/08 §2.3`).
- Un serveur qui transmet les arguments du gabarit (`mlx_lm.server`, llama.cpp) permettrait de baisser le raisonnement. Ce changement relève de `docs/CHAINE.md`, pas de l'application.

### 4.3 La sortie structurée du serveur est écartée

L'API de LM Studio accepte `response_format` de type `json_schema`. Essai du même compte rendu avec ce paramètre : le serveur répond en 26,2 s, `message.content` est vide et le JSON se trouve dans `reasoning_content` (295 tokens de raisonnement sur 296).

Conséquences :

- Le client n'envoie jamais `response_format`. Il demande le JSON par un message système qui donne le schéma, valide la réponse avec pydantic et relance deux fois au plus (`specs/04 §3`). C'est le choix du kit, confirmé par la mesure.
- Ce comportement ne dépend d'aucun serveur particulier. `mlx_lm.server` ou llama.cpp conviennent sans changer de code.

### 4.4 Le nom du modèle dans les réglages est un alias

Le kit fixe `model = "qwen-local"` avec l'adresse du port 8080. `qwen-local` est le nom servi par la passerelle du port 4000. LM Studio sert le modèle sous l'identifiant `qwen3.8-27b`. Mesure : un appel direct au port 8080 avec `model = "qwen-local"` est servi par le modèle chargé, et la réponse nomme `qwen3.8-27b`.

Conséquences :

- La valeur par défaut `qwen-local` est gardée. Elle fonctionne tant qu'un seul modèle est chargé dans LM Studio.
- Le journal note le nom envoyé (`agent_run.model`), pas le nom renvoyé.
- Avec un serveur qui refuse un nom inconnu, l'utilisateur écrit le nom exact dans `reglages.toml`.

### 4.5 Le serveur local accepte les images

Le kit laissait ce point ouvert. Une image PNG unie rouge envoyée en URI de données à LM Studio avec Qwen3.8 27B a été décrite « Rouge ». La fiche du modèle le classe en `image-text-to-text`.

Conséquences :

- La tâche V3-03 (lecture d'une capture d'écran) est maintenue dans le lot 3.
- La qualité de lecture d'un tableau dense n'est pas mesurée. Elle le sera sur l'échantillon anonymisé du lot 3. La relecture par l'utilisateur reste obligatoire quelle que soit la source.

### 4.6 Python 3.12 au minimum, 3.13 sur la machine

Le kit dit « Python 3.12 ». Le `python3` du système est en 3.9.6. Celui qui sert au projet est Python 3.13.16 (`~/.local/bin/python3`). Le plancher le plus haut exigé par un paquet du tableau 4.11 est Python 3.11, et `mlx` 0.32.4 publie des roues de cp310 à cp315.

Conséquences :

- `pyproject.toml` déclare `requires-python = ">=3.12"`. Le code n'utilise aucune syntaxe propre à 3.13.
- ruff vise `py312` (`target-version`).

### 4.7 Le client de test de FastAPI annonce l'abandon de httpx

`fastapi.testclient.TestClient` vient de starlette. starlette 1.10.0 liste `httpx2` et `httpx` dans son option `full`, et émet un avertissement de dépréciation quand `TestClient` s'appuie sur `httpx`. `httpx2` n'est pas dans la liste des dépendances autorisées.

Conséquences :

- `httpx` reste la bibliothèque du client du modèle et des tests (`httpx.MockTransport`). `httpx2` n'est pas ajouté.
- `pyproject.toml` ne transforme pas les avertissements en erreurs et borne `httpx` à `>=0.27,<0.29`, la plage acceptée par starlette 1.10.0.
- Le jour où starlette retire `httpx`, l'architecte tranche entre ajouter `httpx2` et borner starlette.

### 4.8 Les règles de ruff sont fixées dans le dépôt

Avec ruff 0.17.6, `ruff check .` sans configuration signale des règles de simplification, de tri d'imports et de modernisation (`SIM117`, `I001`, `UP036`, entre autres). Le kit ne fixait aucune règle.

Conséquences :

- `pyproject.toml` fixe `select = ["E4", "E7", "E9", "E501", "F", "I", "UP", "B", "SIM"]`, `line-length = 100` et `target-version = "py312"` (`specs/01 §5`).
- `scripts/ruff.toml` et `tests/contrats/ruff.toml` fixent les mêmes règles pour les fichiers que l'exécutant ne peut pas modifier.

### 4.9 Une application typer à une seule commande ignore le nom de la commande

Avec typer, une application qui ne déclare qu'une commande l'exécute directement. `cabinet version` échoue alors sur un argument en trop. Le lot 0 ne crée que `version` dans sa première tâche.

Conséquence : `cli.py` déclare une fonction de rappel vide (`@app.callback()`) dès la tâche V0-01, ce qui force le mode à sous-commandes (`specs/03 §5`).

### 4.10 SQLite ne garde pas le fuseau d'une date

Le kit demande des heures « stockées avec leur fuseau » et un agenda en `Europe/Zurich`. Le type `DateTime(timezone=True)` de SQLAlchemy ne garde pas le fuseau avec SQLite.

Conséquences :

- Un type `TZDateTime` écrit en UTC sans fuseau et relit dans `Europe/Zurich` (`specs/02 §1.1`).
- Un module `clock.py` est la seule source de l'heure et du jour courants (`specs/01 §3.6`). Les tests y injectent la date.

### 4.11 Versions de référence (relevées le 24.11.2026 sur PyPI)

| Paquet | Version | Python requis | Licence déclarée | Note |
| --- | --- | --- | --- | --- |
| fastapi | 0.165.0 | 3.11 | MIT | demande starlette 1.6.0 ou plus |
| starlette | 1.10.0 | 3.11 | BSD-3-Clause | dépendance de fastapi, fournit `TestClient` |
| uvicorn | 0.54.0 | 3.10 | BSD-3-Clause | |
| jinja2 | 3.1.6 | 3.7 | BSD | |
| python-multipart | 0.0.34 | 3.11 | Apache-2.0 | lecture des formulaires |
| sqlalchemy | 2.1.9 | 3.11 | MIT | style déclaratif 2.x |
| pydantic | 2.14.4 | 3.10 | MIT | |
| httpx | 0.28.1 | 3.8 | BSD-3-Clause | client du modèle local |
| typer | 0.29.2 | 3.11 | MIT | |
| icalendar | 7.3.2 | 3.10 | BSD-2-Clause | |
| recurring-ical-events | 3.8.2 | 3.8 | LGPL-3.0-or-later | demande icalendar de 6.1.0 à moins de 8.0.0 |
| python-docx | 1.2.0 | 3.9 | MIT | |
| pyyaml | 6.0.4 | 3.10 | MIT | |
| beautifulsoup4 | 4.15.0 | 3.7 | MIT | |
| mlx-whisper | 0.4.3 | 3.8 | MIT | option `audio`. Dépend de `mlx`, `numpy` et `torch` |
| pytest | 9.2.0 | 3.10 | MIT | développement |
| ruff | 0.17.6 | 3.7 | MIT | développement |

Python 3.12 est le plancher retenu : c'est une version que tous les paquets du tableau acceptent, et la machine de Thomas tourne en 3.13.16. `pyproject.toml` déclare des bornes basses égales aux versions du tableau, sans borne haute, sauf pour `httpx` (section 4.7), `sqlalchemy` (`<3`) et `icalendar` (`<8`, imposé par `recurring-ical-events`).

`recurring-ical-events` est sous licence LGPL. L'application l'utilise sans le modifier et n'est pas distribuée. Ce constat n'est pas un avis juridique : si Cabinet devait être diffusé, le point serait à faire valider par une personne compétente.

L'option `audio` installe `torch` par dépendance de `mlx-whisper`. Son poids et sa tenue en mémoire à côté du modèle de langage ne sont pas mesurés.

### 4.12 Précisions de contrat issues de la préparation du lot 0

L'écriture des tests de contrat de la vague 0 a relevé sept silences du kit (`revues/vague-0-questions.md`). Ils sont tranchés ici, avec une contradiction relevée en rédigeant cette suite.

| Sujet | Décision | Détail |
| --- | --- | --- |
| Module des types du modèle | `Message`, `LLMResult`, `ConfigError` et `LLMError` dans `llm/types.py`. `assert_loopback` et `LocalLLM` dans `llm/client.py`. Le protocole `LLM` dans `ports/llm.py` | `specs/04 §1` |
| Retour de `get_session()` | Une `Session` neuve, liée au moteur courant. L'appelant la ferme | `specs/05 §2.1` |
| Durabilité du journal | `record_run` valide lui-même la ligne à la création, à la sortie normale et à l'erreur. En erreur il annule d'abord le travail en cours de la session | `specs/05 §2.2` |
| Moteur et `CABINET_HOME` | Un moteur par chemin de base, résolu à chaque appel | `specs/05 §2.1` |
| Date de fin d'une exécution en erreur | `ended_at` est renseignée dans les deux cas | `specs/02 §2.1` |
| Jeux de données | Ceux du relecteur dans `tests/contrats/fixtures/`, ceux de l'exécutant dans `tests/fixtures/` | `specs/09 §2` |
| Dépendances déclarées dès V0-01 | Toute la liste d'exécution du tableau 4.11. L'option `audio` arrive avec V2-03 | `specs/10`, V0-01 |
| Adresse de `cabinet llm-test` avant la configuration | Valeurs par défaut de `specs/03 §3`, puis `load_settings()` à partir de V1-01 | `specs/03 §5` |
| Modification d'une action après validation (V2-06 l'interdisait, V2-07 la demandait) | Le contenu se fige à la validation. Le suivi (responsable, échéance, état) reste modifiable depuis la page Actions | `specs/08 §4.3` |

Deux valeurs du kit sont corrigées au passage. L'échéance des imputations n'a pas de valeur par défaut (`cra.deadline` est vide, le kit montrait `2026-11-30` comme exemple). Le réglage `ui.port` est affiché mais ne pilote pas `make run`, qui fixe le port 8765.

### 4.13 Hypothèses encore ouvertes

| Hypothèse | Qui vérifie | Quand |
| --- | --- | --- |
| Claude Code relié à Qwen par LiteLLM termine trois tâches sans aide | Thomas, relecteur | Lot 0 |
| L'agenda s'exporte ou se publie en ICS depuis la messagerie de Thomas | Thomas | Avant la recette du lot 1 |
| Un transcript Teams en `.docx` a la forme supposée (une ligne orateur et horodatage, puis le texte) | Relecteur, sur un exemple anonymisé | Phase A du lot 2 |
| `mlx-whisper` tient en mémoire à côté du modèle de langage (22,8 Go) | Thomas | Recette du lot 2 |
| Le délai de 900 secondes suffit pour une réunion d'une heure | Thomas | Recette du lot 2 |
| Les règles R1 à R4 correspondent au contrôle que Thomas fait à la main | Thomas | Avant le lot 3 |
| La structure des écrans de l'outil d'imputation | Thomas fournit un échantillon anonymisé | Avant le lot 3, bloquant |
| Qwen lit correctement un tableau dense dans une capture d'écran | Relecteur, sur l'échantillon | Phase A du lot 3 |

### 4.14 Points ouverts hors du développement

1. La validation juridique de l'enregistrement et de la transcription de réunions, en interne et chez les clients, reste à obtenir par Thomas auprès des personnes compétentes avant tout usage sur une réunion réelle. La case de consentement est une garde technique. Ni l'architecte ni cette spécification ne tiennent lieu d'avis juridique.
2. Le cadre d'usage de la machine doit permettre explicitement le traitement de contenus de réunions et d'imputations nominatives de l'entreprise et de ses clients.
3. La demande d'accès Microsoft Graph en lecture seule est à lancer en parallèle si Thomas veut la version 2.
4. La durée de conservation des transcripts et des comptes rendus dans la base locale n'est pas fixée. La version 1 ne supprime que l'audio.
5. Le dépôt distant `tomouxx/app-thomas` est public. Il ne contient que le kit, le code et des données synthétiques.

### 4.15 Ajouts au kit

Ces éléments n'étaient pas dans le kit du 23.11.2026. Chacun comble un vide qui aurait obligé l'implémenteur à décider seul. Thomas peut contester chacun d'eux.

| Ajout | Raison | Détail |
| --- | --- | --- |
| Module `clock.py` | Sans source unique de l'heure, les tests de « J-3 » ou de « dans 3 jours » ne peuvent pas fixer la date | `specs/01 §3.6` |
| Type `TZDateTime` | SQLite ne garde pas le fuseau | `specs/02 §1.1` |
| Compteurs de tokens dans `AgentOutcome` | Le kit demandait de journaliser le modèle et les tokens sans dire comment un agent les transmet | `specs/05 §2.3` |
| Reprise d'un verrou périmé | Un redémarrage pendant un passage aurait bloqué tous les passages suivants. La machine a redémarré en pleine session le 24.11.2026 | `specs/05 §3.2` |
| Bouton « Réessayer » sur un élément en erreur | Le kit ne prévoyait aucune sortie de l'état `erreur` autre que `ignore` | `specs/08 §3.4` |
| Rangement des captures lues dans `traite/` | Le kit ne rangeait que les transcripts et les audios | `specs/05 §4` |
| Lignes ignorées d'une capture gardées dans `inbox_item.error` | `CaptureData.warnings` n'était stocké nulle part, et l'utilisateur ne les voyait pas | `specs/07 §4.1` |
| Gardes d'hôte et d'origine | Écouter sur `127.0.0.1` n'empêche pas une page web ouverte dans le navigateur d'appeler l'application | `specs/08 §1.3` |
| Pages de documentation de FastAPI désactivées | Elles chargent des scripts depuis un CDN, ce que le principe 2 interdit | `specs/08 §1.1` |
| Gabarits `settings.html` et `error.html` | La page Réglages et les erreurs 4xx n'avaient pas de gabarit | `specs/08 §9` |
| Contrôle relancé quand l'utilisateur modifie un extrait | Sans cela, un extrait retouché à la main gardait sa marque « vérifié » | `specs/06 §8.1` |
| Classe `CalendarError` et messages d'erreur fixés | Le kit demandait « une erreur au message clair » sans la nommer | `specs/01 §6` |
| Texte exact des prompts, des rendus Markdown et des relances | Le kit en donnait le contenu, pas la forme | `specs/06 §4.1`, `§7`, `§9.2`, `specs/07 §6.1` |

## 5. Principes de conception

Ces sept règles priment sur toute tâche du plan. Une tâche qui semble les contredire est mal lue ou mal écrite, et l'implémenteur s'arrête.

1. Brouillons seulement. Le code ne contient aucune fonction d'envoi : ni SMTP, ni appel Graph d'envoi, ni automatisation de la messagerie. Un test cherche `smtplib`, `sendMail` et `send_message` dans `src/` et n'en trouve aucun.
2. Local seulement. L'adresse du modèle doit être une adresse de bouclage. Le chargement de la configuration et le constructeur du client refusent toute autre adresse (`specs/04 §2`).
3. Consentement avant audio. Un fichier audio n'est transcrit que si l'utilisateur a coché, pour ce fichier, qu'il a l'accord des participants. Le serveur le contrôle deux fois : à la page Entrée et dans l'agent de transcription.
4. Pas de donnée réelle dans le dépôt. Les jeux de test sont synthétiques, avec des noms fictifs et le domaine `exemple.test`. Les données réelles vivent dans le répertoire de données.
5. Tout est journalisé. Chaque exécution d'agent laisse une ligne dans `agent_run`, y compris en cas d'erreur.
6. Pas de modèle de langage là où une règle suffit. Le découpage des fichiers, le rattachement aux réunions, les règles de contrôle et les relances sont déterministes. Le modèle sert à extraire un compte rendu, à proposer un ordre du jour et à lire une capture d'écran.
7. Le texte lu est une donnée. Un transcript, une note ou une capture ne sont jamais traités comme des instructions, et chaque prompt le dit au modèle par la phrase de garde de `specs/04 §4`.

## 6. Glossaire

| Terme | Définition |
| --- | --- |
| Agent | Composant du pipeline qui a un nom, s'exécute dans une session de base et renvoie un `AgentOutcome` |
| Brouillon | Message préparé (`draft`), jamais envoyé par l'application |
| Capture | Fichier tiré de l'outil d'imputation : page HTML, texte copié ou capture d'écran. Son nom commence par `app_` |
| Compte rendu | `minutes` : résumé, décisions, actions, points ouverts, prochaine séance |
| Consentement | Case cochée par l'utilisateur, pour un fichier audio donné, qui atteste l'accord des participants |
| CRA | Compte rendu d'activité, autre nom des imputations mensuelles |
| Élément d'entrée | `inbox_item` : un fichier déposé, connu par son empreinte sha256 |
| Extrait | Citation mot pour mot de la source, attachée à une décision ou à une action (`quote`) |
| Fiche de préparation | `prep_note` : document préparé avant une réunion suivie |
| Imputation | Jours déclarés par une personne sur un code projet pour un mois |
| Jours attendus | Jours ouvrés du mois, moins les fériés et les absences, multipliés par le taux d'activité |
| Lot | Groupe de tâches du plan. Les lots 0 à 3 sont les vagues 0 à 3 du kit |
| Nature | Type d'un fichier déposé, déduit de son nom (`kind`) |
| Période | Mois d'imputation, au format `AAAA-MM` |
| Phrase de garde | Phrase fixe d'un prompt qui déclare le contenu lu comme une donnée |
| Pipeline | Exécution des agents dans l'ordre, sous un verrou |
| Référentiel d'équipe | Fichier `equipe.yaml` : personnes, taux d'activité, codes prévus, absences, jours fériés |
| Relecteur | Modèle Claude qui écrit les tests de contrat et juge chaque lot. Il n'écrit pas le code de l'application |
| Répertoire de données | Dossier désigné par `CABINET_HOME`, `~/Cabinet` par défaut |
| Série | Ensemble des réunions de même titre normalisé (`series.key`) |
| Série suivie | Série pour laquelle l'utilisateur veut une fiche de préparation |
| Source | Texte d'où sort un compte rendu : transcript Teams, notes ou transcription d'un audio |
| Tâche | Unité du plan, identifiée `V<lot>-<numéro>`. Le kit dit « ticket » |

## 7. Sources vérifiées

Consultées le 24.11.2026.

- Index PyPI, une fiche par paquet du tableau 4.11 : https://pypi.org/pypi/fastapi/json (même forme pour les autres paquets)
- Fiche du modèle de langage : https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-6bit
- Fiche du modèle de transcription : https://huggingface.co/mlx-community/whisper-large-v3-mlx
- Recette SQLAlchemy pour les dates avec fuseau : https://docs.sqlalchemy.org/en/21/core/custom_types.html#store-timezone-aware-timestamps-as-timezone-naive-utc
- Typer, une ou plusieurs commandes : https://typer.tiangolo.com/tutorial/commands/one-or-multiple/
- Sortie structurée de LM Studio : https://lmstudio.ai/docs/developer/openai-compat/structured-output
- Claude Code avec LiteLLM : https://docs.litellm.ai/docs/proxy/client_setup/claude_code
- Connexion de Claude Code à une passerelle : https://code.claude.com/docs/en/llm-gateway-connect.md

Les mesures des sections 4.1 à 4.5 viennent de la machine de Thomas (LM Studio 0.4.15, LiteLLM 1.108.5) et du journal `.chaine/journal.jsonl`, qui n'est pas dans le dépôt.
