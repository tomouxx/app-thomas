# Questions de la phase A, vague 0

Les points de ce document sont tranchés dans `specs/00 §4.12` (décisions du 24.11.2026). Les renvois à `SPEC.md` désignent la version 1.0 du 23.11.2026.

Pour l'architecte. Écrit par le relecteur (Claude Fable 5.2) le 24.11.2026, pendant l'écriture des tests de contrat. Chaque point dit ce que le relecteur a fait en attendant la réponse.

## Critères impossibles à tester tels quels

| Ticket | Critère | Problème | Ce que font les tests | Vérifié où |
| --- | --- | --- | --- | --- |
| V0-01 | 1. `make install` réussit dans un environnement vierge | L'installation appelle PyPI, et un test n'appelle jamais le réseau (`CLAUDE.md`, règle 5) | Ils vérifient la forme de la cible (`.venv`, `pip install -e ".[dev]"`) et le contenu de `pyproject.toml` | Par la boucle avant chaque `make check`, puis en phase B sur un clone neuf |
| V0-01 | 2. `make check` sort avec le code 0 | Un test lancé par `make check` ne peut pas relancer `make check` | Ils vérifient l'ordre des commandes par `make -n -B check` | Par la boucle après chaque session, puis en phase B |
| V0-02 | Contrat : `cabinet llm-test` | Aucun critère numéroté. La commande parle à un vrai modèle, et le ticket ne dit pas d'où vient l'adresse tant que la configuration n'existe pas (V1-01) | Aucun test | Scénario manuel de `PLAN_VAGUES.md`, en phase B |
| V0-02 | 8. Aucun test n'ouvre de connexion réseau | Le critère porte sur les tests de l'exécutant, pas sur le code | Un test bloque les sockets pendant un appel du client. Un autre vérifie que `tests/unit/test_llm_client.py` existe et cite `MockTransport` | Lecture de `tests/unit/` en phase B |
| V0-03 | 4. Rien n'est écrit ailleurs | « Ailleurs » n'a pas de borne | Ils surveillent un faux répertoire personnel (`HOME`) et le dossier courant | Lecture du code en phase B |

## Contradictions et silences

1. **Module des types du modèle de langage.** `SPEC.md` section 5.1 dit que `Message` et `LLMResult` sont définis « dans le module du port correspondant », donc `ports/llm.py`. La section 4.2 et le ticket V0-02 créent `llm/types.py`. Le ticket ne dit pas non plus où vivent `ConfigError`, `LLMError` et `assert_loopback`. Les tests les cherchent dans `cabinet.llm.client`, `cabinet.llm.types` puis `cabinet.ports.llm`, sans en imposer un. À trancher avant la vague 1, où d'autres modules importeront ces noms.
2. **Retour de `get_session()`.** Le ticket V0-03 ne dit pas si `get_session()` renvoie une session, un gestionnaire de contexte ou un générateur. Les tests n'appellent que `get_engine()` et `init_db()` et ouvrent leurs propres sessions.
3. **Durabilité de la trace d'erreur.** Le ticket V0-03 dit qu'un bloc en erreur « laisse une ligne à l'état erreur ». Le test du critère 2 annule la session de l'appelant (`rollback`) puis relit la table dans une session neuve. Il exige donc que `record_run` valide lui-même la ligne. C'est la lecture du principe 5 de `SPEC.md` (« y compris en cas d'erreur »), pas une phrase du ticket. Pour la sortie normale, le test valide la session de l'appelant avant de relire, et n'impose donc rien.
4. **Moteur et `CABINET_HOME`.** Un test change `CABINET_HOME` entre deux appels et attend deux bases distinctes. Une implémentation qui garde un moteur global créé au premier appel échoue. Cette exigence vient de la section 12 de `SPEC.md` (« pas d'état global en dehors de la configuration ») et de la section 4.3. Le ticket V0-03 ne cite pas la section 12.
5. **Qui écrit dans `tests/fixtures/`.** `REVIEW.md` demande au relecteur d'y écrire ses doublures et ses jeux de données. Le tableau de `CLAUDE.md` dit que l'exécutant y écrit et que le relecteur le lit. La vague 0 n'a besoin d'aucun jeu de données : la seule doublure (un faux serveur de modèle) est dans le fichier de test. À trancher avant la vague 1.
6. **`python-multipart`, `icalendar` et les autres dépendances de la section 11.** Le ticket V0-01 ne dit pas si `pyproject.toml` déclare dès maintenant toute la liste de la section 11 ou seulement ce dont la vague 0 a besoin. Les tests n'imposent rien.
7. **Date de fin d'une exécution en erreur.** Le critère 1 de V0-03 exige `ended_at` pour une ligne `ok`. Rien n'est dit pour une ligne `erreur`, alors que la page affiche une durée. Les tests ne l'exigent pas.

## Constats sur la chaîne, hors tickets

1. **Tests de contrat des tickets à venir.** `make check` lance tous les tests. Les contrats de V0-02 et V0-03 feraient donc échouer la vérification de V0-01. `tests/contrats/conftest.py` écarte de la collecte les contrats d'un ticket qui n'est ni « fait » ni en cours. Détail dans `docs/REPRISE.md`, section 5.
2. **`TestClient` et `httpx`.** Avec starlette 1.10.0 (relevé le 24.11.2026 dans un environnement d'essai), `fastapi.testclient` émet un avertissement : l'usage de `httpx` avec `TestClient` est déprécié au profit de `httpx2`. `httpx2` n'est pas dans la liste de la section 11. Ce n'est qu'un avertissement aujourd'hui. À surveiller, ou à épingler dans `pyproject.toml`.
3. **Règles par défaut de ruff.** ruff 0.17.6 signale par défaut des règles de simplification (`SIM117`). Les tests de contrat passent `ruff check .` avec les réglages par défaut. Si l'exécutant durcit la configuration de ruff dans `pyproject.toml`, il devra exclure `tests/contrats/` de ces règles, puisqu'il ne peut pas modifier ces fichiers.
4. **Commande `cabinet version` avec typer.** Une application typer qui n'a qu'une commande l'exécute sans attendre son nom, et `cabinet version` échoue alors sur un argument en trop. Le test du critère 4 de V0-01 lance la vraie commande installée. C'est un piège probable pour l'exécutant, pas un défaut du ticket.

## Vérification des tests eux-mêmes

Les 51 tests de contrat ont été lancés contre une implémentation jetable, écrite hors du dépôt et jamais montrée à l'exécutant, pour vérifier qu'ils sont satisfiables et qu'aucun n'échoue par sa propre faute. Ils passent tous. Cette implémentation n'est dans aucun commit.
