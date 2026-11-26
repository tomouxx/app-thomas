# Tests et recette

Principes de test, répartition entre relecteur et exécutant, doublures, scénarios d'intégration et recette de chaque lot. Les cas propres à un composant sont listés à la fin de sa section, dans le document qui le décrit (paragraphes « Tests »). Le déroulé d'une revue de lot est dans `REVIEW.md`.

## 1. Principes

1. Toute la chaîne se teste sans modèle, sans serveur et sans réseau. `LocalLLM` reçoit un `httpx.MockTransport`. Les agents reçoivent des doublures des ports (section 3). Aucun test n'ouvre de connexion.
2. Aucun test ne lit ni n'écrit dans `~/Cabinet`. Chaque test travaille dans un répertoire temporaire désigné par `CABINET_HOME`.
3. Aucune donnée réelle dans le dépôt. Les jeux de test sont synthétiques : noms fictifs, adresses du domaine `exemple.test`, codes projet inventés (`PROJ-A`).
4. Aucun test ne dépend de la date du jour. Un test qui parle de « dans 3 jours » ou de « J-3 » fixe la date par `cabinet.clock.now` (section 3.3).
5. Les tests de contrat sont écrits par le relecteur avant le lot, à partir des critères de fin du plan. L'exécutant ne les modifie jamais. Il écrit ses propres tests dans `tests/unit/` pour les cas que les contrats ne couvrent pas.
6. Un test de contrat n'utilise que ce que la spécification nomme : fonctions, classes, routes, tables. Il n'impose aucun détail d'implémentation absent de la suite.
7. Un défaut corrigé reçoit un test de non-régression, nommé d'après le symptôme.
8. `make check` lance tous les tests actifs. Rien n'est terminé tant qu'il ne passe pas.

Il n'y a pas de test lourd automatique. Ce qui demande un vrai modèle ou la vraie bibliothèque de transcription se vérifie à la main, à la recette (section 6).

## 2. Arborescence et propriété

```text
tests/
  contrats/                     relecteur. Interdit en écriture à l'exécutant
    conftest.py                 activation par tâche, isolement du répertoire de données
    ruff.toml                   règles de ruff pour ce dossier (specs/00 §4.8)
    test_v0_01_squelette.py     un fichier par tâche : test_<identifiant>_<sujet>.py
    test_v0_02_client_llm.py
    test_v0_03_journal.py
    fixtures/                   jeux de données et doublures du relecteur
      app/                      échantillon anonymisé et jeux d'imputation (lot 3)
  unit/                         exécutant
    conftest.py                 ses fixtures et ses doublures
    test_*.py                   un fichier par tâche, nommé dans le plan
  fixtures/                     jeux de données de l'exécutant
    agenda/  transcripts/  minutes/
```

| Dossier | Écrit par | Lu par |
| --- | --- | --- |
| `tests/contrats/`, dont `tests/contrats/fixtures/` | relecteur | exécutant |
| `tests/unit/`, `tests/fixtures/` | exécutant | relecteur |

Cette répartition remplace celle du kit, où `REVIEW.md` et `CLAUDE.md` se contredisaient sur `tests/fixtures/`. Les jeux du relecteur sont protégés avec ses tests, par les mêmes gardes de la chaîne.

Nommage des tests de contrat :

| Nom du test | Usage |
| --- | --- |
| `test_c<numéro>_<résumé>` | un critère numéroté de la tâche. Au moins un test par critère |
| `test_contrat_<résumé>` | un point du contrat qui n'est pas un critère numéroté |

Activation par tâche. `make check` lance tous les tests, et les contrats d'une tâche pas encore faite échoueraient. `tests/contrats/conftest.py` écarte donc de la collecte le fichier d'une tâche tant qu'elle n'est ni « fait » dans `tickets/ETAT.md`, ni la tâche en cours (variable `CABINET_TICKET`, fixée par la boucle de développement). Avec `CABINET_CONTRATS=tous`, rien n'est écarté : c'est le mode de la revue de lot. Aucun test n'est affaibli par ce mécanisme, décrit dans `docs/REPRISE.md`, section 5.

## 3. Doublures et fixtures clés

Le relecteur et l'exécutant écrivent chacun les leurs, sur ces modèles.

### 3.1 Isolement du répertoire de données

```python
@pytest.fixture(autouse=True)
def environnement_isole(tmp_path, monkeypatch):
    racine = tmp_path / "cabinet-home"
    monkeypatch.setenv("CABINET_HOME", str(racine))
    monkeypatch.delenv("CABINET_ALLOW_REMOTE_LLM", raising=False)
    return racine
```

La fixture ne crée pas le dossier : c'est au code de le faire. Elle retire la variable qui lève la garde de bouclage, pour qu'un test ne dépende pas de l'environnement de la machine.

### 3.2 Doublures des ports

```python
class FakeLLM:
    """Rejoue une liste de réponses. La dernière est répétée si les appels continuent."""
    model = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(self, messages, schema=None, images=None) -> LLMResult:
        self.calls.append({"messages": messages, "schema": schema, "images": images})
        reponse = self.responses[min(len(self.calls), len(self.responses)) - 1]
        if isinstance(reponse, Exception):
            raise reponse
        if isinstance(reponse, BaseModel):
            return LLMResult(text=reponse.model_dump_json(), parsed=reponse,
                             prompt_tokens=10, completion_tokens=5)
        return LLMResult(text=str(reponse))


class FakeTranscriber:
    def __init__(self, result: TranscriptData | Exception):
        self.result = result
        self.calls: list[tuple[Path, str]] = []

    def transcribe(self, audio_path, language="fr") -> TranscriptData:
        self.calls.append((audio_path, language))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
```

Une réponse de `FakeLLM` est une instance du schéma attendu, une chaîne, ou une exception (`LLMError`) à lever. `len(llm.calls)` sert aux tests qui comptent les appels, dont ceux qui vérifient que le modèle n'est pas appelé.

L'agenda se teste avec un fichier ICS synthétique et le vrai `IcsCalendarSource`. Le dossier d'entrée se teste avec un dossier temporaire et le vrai `FolderInbox`. Pour qu'un fichier ne soit pas ignoré comme « modifié à l'instant », le test recule sa date de modification par `os.utime`.

### 3.3 Date fixée

```python
@pytest.fixture
def fixer_date(monkeypatch):
    def fixer(valeur: datetime) -> None:        # valeur avec fuseau
        monkeypatch.setattr("cabinet.clock.now", lambda: valeur)
    return fixer
```

`clock.today()` appelle `clock.now()` et suit donc la date fixée. C'est pourquoi le code importe le module `clock` et non ses fonctions (`specs/01 §3.6`).

### 3.4 Client web

Les routes se testent avec `fastapi.testclient.TestClient(create_app())`, sans suivre les redirections quand le test vérifie un code 303 (`follow_redirects=False`). L'hôte du client de test est `testserver`, accepté par la garde d'hôte (`specs/08 §1.3`).

## 4. Scénarios d'intégration

Chaque scénario enchaîne plusieurs composants réels sur un répertoire temporaire, avec les doublures de la section 3. Le relecteur l'écrit comme un test de contrat rattaché à la dernière tâche qu'il met en jeu.

| Scénario | Étapes | Résultat attendu | Tâche de rattachement |
| --- | --- | --- | --- |
| S1, socle | `cabinet init`, agenda synthétique, un transcript, une note et un audio dans `entree/`, un passage | trois éléments aux états `a_rattacher`, `a_rattacher` et `consentement_requis`, évènements, séries et réunions créés, une ligne `ok` au journal pour `ingestion` et pour `agenda` | V1-08 |
| S2, passage répété | deux passages de suite sur les mêmes fichiers | aucun doublon d'élément, d'évènement, de série ni de réunion | V1-08 |
| S3, verrou | un passage lancé pendant qu'un autre tient le verrou | `PipelineBusy`, verrou retiré à la fin du premier | V1-08 |
| S4, consentement | rattacher l'audio sans la case, puis avec | 400 et état inchangé, puis `pret` avec `consent_at`. Un audio `pret` sans consentement n'atteint jamais le transcripteur | V2-03 |
| S5, réunion complète | transcript rattaché, passage avec `FakeLLM` dont un extrait est inventé, tentative de validation, confirmation, validation | compte rendu version 1, un élément « à vérifier », 409 à la première validation, puis compte rendu `valide`, fichier d'export, brouillon `envoi_cr`, action visible à la page Actions | V2-07 |
| S6, modèle en panne | passage avec un `FakeLLM` qui lève `LLMError`, puis passage avec un `FakeLLM` qui répond | aucune ligne `minutes` après le premier passage, agent `minutes` à l'état `ok` avec une erreur comptée, compte rendu créé au second passage | V2-04 |
| S7, fiche | série suivie, une réunion validée, réunion suivante dans 3 jours, passage | fiche avec le résumé, les actions ouvertes de la série et l'ordre du jour | V2-08 |
| S8, source longue | source de 30 000 mots, passage | trois morceaux, quatre appels au modèle, un seul compte rendu | V2-09 |
| S9, imputations | capture texte, passage, relecture, passage, modification d'une ligne | capture non relue sans anomalie, puis anomalies et brouillons de relance, puis capture non relue et anomalies supprimées | V3-06 |
| S10, aucun envoi | recherche dans `src/` | aucune occurrence de `smtplib`, `sendMail` ni `send_message` | V1-07 |

## 5. Contrôles transversaux

Ces contrôles ne tiennent à aucun composant. Le relecteur les vérifie à chaque revue de lot, et la plupart sont aussi des tests.

| Contrôle | Commande ou test | Depuis |
| --- | --- | --- |
| Aucune fonction d'envoi | `grep -rn "smtplib\|sendMail\|send_message" src/` ne renvoie rien | V1-07 |
| Chemin des données dans un seul module | `~/Cabinet` n'apparaît dans `src/` que dans `config.py` | V1-01 |
| Heure du système dans un seul module | `grep -rn "datetime.now\|date.today" src/` ne renvoie que `clock.py` | V0-03 |
| Aucune ressource externe | aucun `http://` ni `https://` dans `web/templates/` et `web/static/` | V1-05 |
| Transcription importée à la demande | `mlx_whisper` absent de `sys.modules` après `import cabinet.web.app` | V2-03 |
| Garde de bouclage en place | les tests de `specs/04 §2` passent, et `load_settings` refuse une adresse distante | V0-02, V1-01 |
| Consentement contrôlé deux fois | scénario S4 | V1-06, V2-03 |
| Journal complet | chaque agent du pipeline laisse une ligne `agent_run` par passage | V1-08 |
| Dépendances | `pyproject.toml` ne déclare que les paquets de `specs/00 §4.11` | chaque lot |
| Arborescence | `src/` ne contient que les fichiers de `specs/01 §2` | chaque lot |
| Fichiers protégés | `scripts/verifier_garde.sh N` répond OK | chaque lot |
| Données réelles | aucun domaine de messagerie réel dans `tests/` | chaque lot |

## 6. Recette

Chaque lot se termine par une recette manuelle, jouée d'abord par le relecteur sur des données synthétiques, puis par Thomas. Un lot n'entre dans `main` qu'après un rapport au verdict GO et l'accord de Thomas.

### 6.1 Lot 0, preuve de la chaîne

| Étape | Critère d'acceptation |
| --- | --- |
| `make install` sur un clone neuf | l'environnement `.venv` est créé, la commande sort avec 0 |
| `CABINET_CONTRATS=tous make check` | ruff ne signale rien, tous les tests passent |
| `make run`, puis ouvrir `http://127.0.0.1:8765/sante` | `{"statut": "ok", "version": "0.1.0"}` |
| Ouvrir `/journal` | la page s'affiche, vide |
| `cabinet llm-test`, serveur du modèle allumé | `OK` |
| `cabinet llm-test`, serveur éteint | un message d'erreur en français, code de sortie 1 |

Mesures à noter dans le rapport : tâches terminées sans aide, nombre d'interventions de Thomas, tâches bloquées, durée totale. Seuil pour continuer avec ce harnais : les trois tâches sont terminées et Thomas est intervenu deux fois au plus. En dessous, on change l'outil qui pilote Qwen avant d'aller plus loin (`docs/CHAINE.md`).

### 6.2 Lot 1, socle

| Étape | Critère d'acceptation |
| --- | --- |
| `cabinet init` sur un répertoire d'essai | cinq dossiers et `config/reglages.toml` |
| Copier un fichier ICS synthétique dans `config/agenda.ics` et trois fichiers dans `entree/` (transcript, note, audio), puis « Traiter maintenant » | les trois fichiers apparaissent à la page Entrée, les réunions de la semaine au tableau de bord |
| Rattacher le transcript à une réunion | l'élément passe à `pret` |
| Rattacher l'audio sans cocher la case | refus, l'élément reste à `consentement_requis` |
| Créer un brouillon à la main dans la base, le valider | le lien « Ouvrir dans la messagerie » ouvre un message prérempli, et le fichier d'export existe |
| Ouvrir `/journal` | une ligne par agent et par passage |

Avant sa propre recette, Thomas vérifie que son agenda s'exporte en ICS (`specs/00 §4.13`).

### 6.3 Lot 2, réunions

Avec le serveur du modèle allumé et des données synthétiques.

| Étape | Critère d'acceptation |
| --- | --- |
| Déposer le transcript d'une réunion fictive, le rattacher, traiter | un compte rendu brouillon avec résumé, décisions, actions |
| Depuis la page de la réunion, remplacer l'extrait d'une décision par un texte inventé | la décision est marquée « à vérifier » et la validation est refusée |
| Confirmer ou supprimer, puis valider | compte rendu validé, brouillon d'envoi créé, actions à la page Actions |
| Marquer la série « suivie », créer la réunion suivante dans les 7 jours, traiter | une fiche de préparation avec les actions ouvertes |
| Déposer un audio synthétique, cocher le consentement, traiter (option `audio` installée) | un transcript de source `audio`, puis l'audio supprimé à la validation |
| Noter la durée d'un passage et la mémoire occupée | les deux chiffres figurent dans le rapport |

Le premier essai sur des données réelles a lieu après cette recette, par Thomas, sur sa machine, sans le relecteur. Il commence par ses propres notes avant tout enregistrement, et après avoir traité le point juridique de `specs/00 §4.14`.

### 6.4 Lot 3, imputations

| Étape | Critère d'acceptation |
| --- | --- |
| Déposer une capture synthétique, traiter | une capture « à relire » avec ses lignes |
| Relire, traiter | des anomalies conformes à un calcul fait à la main pour deux personnes |
| Ouvrir la page Brouillons | un brouillon de relance par personne en anomalie, au bon destinataire |
| Modifier une ligne de la capture relue | la capture redevient « à relire », ses anomalies disparaissent |
| Enregistrer une échéance | la ligne `deadline` de `reglages.toml` change, le reste du fichier est intact |
| Déposer une capture d'écran synthétique, traiter | une capture de source `vision`, à relire |

## 7. Ce qu'un implémenteur doit garder en tête

- Un test qui échoue parce qu'un fichier est « modifié à l'instant » oublie de reculer sa date de modification.
- Un test qui dépend de la date du jour finit par échouer un lundi ou un premier du mois. Fixer la date.
- Affaiblir un test pour le faire passer est interdit : ni suppression, ni marquage ignoré, ni assertion élargie.
- Les tests de contrat sont protégés. Si l'un d'eux semble faux, l'exécutant l'écrit dans `tickets/notes/` et continue.
- `FakeLLM` rend l'instance du schéma demandé. Un test qui lui fait renvoyer une chaîne ne teste pas la validation, qui appartient à `LocalLLM`.
- Les jeux de test restent synthétiques, y compris ceux tirés de l'échantillon d'imputations : noms, codes et chiffres remplacés, structure gardée.
