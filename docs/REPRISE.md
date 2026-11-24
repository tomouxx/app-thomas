# Reprise de la chaîne

Ce document sert à qui reprend le projet en cours de route, humain ou agent. Il dit comment la chaîne est branchée sur la machine de Thomas, comment la lancer, la surveiller, l'arrêter et la reprendre. Les règles de fond restent dans `CLAUDE.md`, `REVIEW.md` et `docs/EXECUTANT.md`.

État au 24.11.2026.

## 1. Ce qui a changé depuis le kit du 23.11.2026

Le kit d'origine prévoyait une session interactive de l'exécutant, lancée à la main par `scripts/executant.sh`, qui faisait elle-même `make check` et le commit. La chaîne est maintenant pilotée par un script.

| Sujet | Kit du 23.11 | Maintenant |
| --- | --- | --- |
| Lancement de l'exécutant | Session interactive, tous les tickets d'une vague | `scripts/boucle_executant.py`, une session non interactive par ticket |
| `make check` qui fait foi | Celui que l'exécutant dit avoir lancé | Celui que la boucle relance après la session |
| Commit et `tickets/ETAT.md` | Faits par l'exécutant | Faits par la boucle |
| Fichiers protégés | Contrôle après coup par `scripts/verifier_garde.sh` | Refus d'écriture pendant la session, restauration après, puis le même contrôle après coup |
| Tests de contrat des tickets à venir | Rouges jusqu'à leur ticket | Écartés de la collecte tant que leur ticket n'est ni en cours ni « fait » (section 5) |
| Serveurs | Lancés à la main | `scripts/chaine.sh demarrer` |
| Suivi | Aucun | `scripts/suivi.py`, page sur `http://127.0.0.1:8770/` |

`scripts/executant.sh` reste utile pour ouvrir une session interactive de diagnostic. Il ne sert pas à traiter une vague.

## 2. Ce qui est installé sur la machine

| Élément | Où | Remarque |
| --- | --- | --- |
| Python 3.13.16 | `~/.local/bin/python3`, lien vers l'installation gérée par uv | `/usr/bin/python3` est en 3.9.6 et ne convient pas |
| Modèle Qwen3.8 27B, MLX 6 bits | LM Studio, dépôt `lmstudio-community/Qwen3.8-27B-MLX-6bit` | 22,8 Go. Servi sous l'identifiant `qwen3.8-27b` |
| Serveur du modèle | LM Studio, `127.0.0.1:8080` | Lancé par `lms server start` |
| Passerelle LiteLLM 1.108.5 | `~/.cabinet-chaine/venv`, `127.0.0.1:4000` | Hors du dépôt. Journal dans `~/.cabinet-chaine/journaux/litellm.log` |
| Clé de la passerelle | `~/.cabinet-chaine/cle-passerelle` | LiteLLM refuse de démarrer sans clé. `scripts/chaine.sh` en tire une au hasard au premier lancement. Elle ne sort pas de la machine et n'est jamais dans le dépôt |
| Réglages Claude Code de l'exécutant | `~/.cabinet-chaine/claude-executant` | Répertoire vide et séparé : aucun greffon, aucun serveur MCP, aucune mémoire de Thomas |

Rien de tout cela n'est dans le dépôt. Le dépôt ne contient que la configuration de la passerelle (`chaine/litellm.config.yaml`) et les scripts.

## 3. Lancer une vague

```
scripts/chaine.sh demarrer                 # modèle sur 8080, passerelle sur 4000
nohup python3 scripts/suivi.py >/dev/null 2>&1 &
git switch vague-N                         # la phase A du relecteur doit être faite
nohup python3 scripts/boucle_executant.py N > .chaine/boucle.log 2>&1 &
```

La boucle refuse de démarrer si un prérequis manque et dit lequel : `python3` en 3.12 ou plus, commande `claude`, identité git, branche `vague-N`, étiquette `vague-N-tests`, ports 8080 et 4000, arbre de travail propre.

Pour chaque ticket « à faire » dont les dépendances sont « fait », dans l'ordre de `tickets/ETAT.md` :

1. Elle lance `claude -p` avec la consigne du ticket. La session n'a que les outils Bash, Read, Edit, Write, Glob et Grep. Les commandes autorisées sont listées dans `AUTORISES`, en tête du script. Toute autre commande est refusée sans rien demander.
2. Elle restaure les fichiers protégés que la session aurait touchés et le note (évènement `garde`).
3. Elle lance `make install` si `.venv` manque ou si `pyproject.toml` ou `Makefile` ont changé, puis `make check`.
4. Elle exige que les tests de contrat du ticket aient tourné et réussi pendant ce `make check` (rapport écrit par `tests/contrats/conftest.py`).
5. Si tout passe : commit `[ID] titre`, puis commit `[VN-etat] ID fait` qui note l'empreinte dans `tickets/ETAT.md`.
6. Sinon : nouvelle session avec la fin de la sortie en échec. Trois sessions au plus par ticket.
7. Après trois échecs : `git stash` des modifications, note factuelle dans `tickets/notes/ID.md`, ticket « bloqué », commit `[VN-etat] ID bloqué`.

La boucle s'arrête quand plus aucun ticket de la vague n'est prêt. Un ticket dont une dépendance est bloquée reste « à faire » et apparaît comme « sauté » dans le journal.

## 4. Surveiller

- Page de suivi : `http://127.0.0.1:8770/`. Les mêmes données en JSON : `http://127.0.0.1:8770/etat.json`.
- Journal de la chaîne : `.chaine/journal.jsonl`, une ligne JSON par évènement.
- Sortie complète de chaque session : `.chaine/sessions/ID-tN.jsonl`.
- Sorties de `make install` et `make check` : `.chaine/verifs/`.
- Sortie de la boucle : `.chaine/boucle.log`.

Le dossier `.chaine/` est ignoré localement (`.git/info/exclude`). Il ne part jamais dans un commit.

## 5. Tests de contrat et tickets à venir

`make check` lance tous les tests. Sans précaution, les tests de contrat de V0-02 feraient échouer la vérification de V0-01. `tests/contrats/conftest.py` règle ce point sans affaiblir aucun test.

- Un fichier `tests/contrats/test_v0_02_*.py` appartient au ticket V0-02.
- Il est collecté si le ticket est « fait » dans `tickets/ETAT.md`, ou s'il est le ticket en cours (variable `CABINET_TICKET`, fixée par la boucle), ou si `CABINET_CONTRATS=tous`.
- Sinon il est écarté de la collecte.

Le relecteur lance la phase B avec `CABINET_CONTRATS=tous make check`, pour que rien ne soit écarté.

## 6. Arrêter et reprendre

- Arrêter la boucle : `kill $(cat .chaine/boucle.pid)`. La session en cours est arrêtée avec elle. L'arbre de travail reste tel quel.
- Reprendre en gardant le travail en cours : `python3 scripts/boucle_executant.py N --garder`.
- Reprendre en repartant du dernier commit : `python3 scripts/boucle_executant.py N --nettoyer`. Les modifications vont dans `git stash`.
- Rejouer un ticket bloqué après un réglage de la chaîne : `python3 scripts/boucle_executant.py --remettre ID`, commit de `tickets/ETAT.md`, puis relance de la boucle.
- Arrêter les serveurs : `scripts/chaine.sh arreter`.

## 7. Quand la chaîne coince

Ce qu'on a le droit de régler, parce que cela ne dit rien de la qualité de l'exécutant :

- la passerelle (`chaine/litellm.config.yaml`), le serveur du modèle, la taille du contexte ;
- les options de `claude` et la liste des commandes autorisées dans `scripts/boucle_executant.py` ;
- le délai d'une session (`--delai-session`) ;
- la consigne envoyée à l'exécutant, tant qu'elle ne contient ni code ni solution.

Ce qu'on ne fait jamais :

- écrire ou corriger du code dans `src/`, `tests/unit/`, `Makefile`, `pyproject.toml` ou `.gitignore` ;
- souffler la solution d'un ticket dans la consigne ;
- affaiblir un test de contrat pour faire passer un ticket.

Chaque réglage se note, parce qu'il compte dans les mesures de la vague 0 :

```
python3 scripts/boucle_executant.py --intervention "ce qui a été changé et pourquoi" --auteur relecteur
```

Les interventions de Thomas se notent de la même façon, avec `--auteur Thomas` (valeur par défaut).

## 8. Raisonnement du modèle et durée des sessions

Qwen3.8 raisonne par défaut au niveau le plus élevé (`xhigh` dans son gabarit de conversation). LM Studio 0.4.15 ne transmet ni `reasoning_effort` ni `chat_template_kwargs` à ce modèle : les essais du 24.11.2026 n'ont montré aucun effet, et l'API native répond que le modèle n'expose pas de réglage de raisonnement. Un seul tour peut donc dépenser plus de 10 000 tokens de raisonnement, soit un quart d'heure à 14 tokens par seconde.

La boucle s'y adapte sans toucher au modèle : 120 minutes par session, 32 000 tokens de sortie par tour, et lecture de `scripts/`, `chaine/` et `revues/` refusée à l'exécutant. Pour baisser le raisonnement, il faudrait un serveur qui transmet les arguments du gabarit (`mlx_lm.server` ou llama.cpp avec `reasoning_effort` à `medium`).

## 9. Mesures de la vague 0

Le rapport `revues/vague-0.md` reprend les mesures de `PLAN_VAGUES.md` : tickets terminés sans aide, interventions, tickets bloqués, durée. La page de suivi les calcule à partir du journal. Un ticket « terminé sans aide » est un ticket « fait » sans intervention entre son premier lancement et son commit.

Seuil proposé par `PLAN_VAGUES.md` : trois tickets terminés et deux interventions de Thomas au plus.

## 10. Dépôt distant

Le dépôt est poussé sur `https://github.com/tomouxx/app-thomas`. Ce dépôt est public. Il ne doit donc contenir que le kit, le code et des données synthétiques, ce qui est déjà la règle du projet.
