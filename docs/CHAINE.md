# Chaîne de développement

Ce document décrit comment faire travailler ensemble le modèle local, Claude Code et le relecteur. Tout ce qui est écrit ici est une proposition à éprouver en vague 0. Ce qui a été mesuré le 24.11.2026 est en `specs/00 §4.1` à `§4.5`. Ce que le relecteur a installé et réglé sur la machine de Thomas le 24.11.2026 est décrit dans `docs/REPRISE.md`.

## Les quatre rôles

| Rôle | Qui | Où | Ce qu'il produit |
| --- | --- | --- | --- |
| Architecte | Claude, dans la conversation de cadrage | hors du dépôt | La spécification, les tickets, les arbitrages entre deux vagues |
| Exécutant | Qwen3.8 27B, piloté par Claude Code, une session par ticket lancée par `scripts/boucle_executant.py` | dépôt local | Le code et ses tests unitaires |
| Relecteur | Un modèle Claude, dans Claude Code | dépôt local | Les tests de contrat, le réglage de la chaîne et le rapport de vague |
| Commanditaire | Thomas | partout | Les lancements, les décisions, l'accord final sur chaque vague |

## Ce qui tourne sur la machine

Trois processus, tous liés à l'adresse de bouclage.

1. Le serveur du modèle, compatible avec l'API de complétion de conversation d'OpenAI, sur le port 8080. Sur la machine de Thomas c'est LM Studio, qui sert la conversion MLX 6 bits de Qwen3.8-27B sous l'identifiant `qwen3.8-27b`. D'autres serveurs conviennent (`mlx_lm.server --model <dépôt du modèle>`, llama.cpp). LM Studio accepte les images avec ce modèle, vérifié le 24.11.2026. Avec un autre serveur, vérifie-le avant le ticket V3-03.
2. La passerelle LiteLLM sur le port 4000, lancée avec `litellm --config chaine/litellm.config.yaml --host 127.0.0.1 --port 4000`. Elle traduit le format de messages attendu par Claude Code vers le serveur du modèle.
3. Claude Code côté exécutant, lancé par `scripts/boucle_executant.py`, une session non interactive par ticket. Le script fixe `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL` et un répertoire de réglages séparé pour ces sessions seulement.

`scripts/chaine.sh demarrer` allume les deux premiers. `scripts/chaine.sh arreter` les éteint.

Le relecteur est une session Claude Code ordinaire, sans ces variables.

Réglages conseillés pour le serveur du modèle, à ajuster après mesure : fenêtre de contexte d'au moins 64 000 tokens (le harnais envoie un prompt système et des descriptions d'outils volumineux), 128 000 si le mode de raisonnement est actif.

## Points à régler en vague 0

- Claude Code appelle aussi un petit modèle pour des tâches annexes. Il faut que la passerelle sache répondre à ces appels. La documentation LiteLLM a un tutoriel sur l'usage de Claude Code avec des modèles d'autres fournisseurs, qui traite ce point.
- La qualité des appels d'outils de Qwen dans ce harnais est inconnue. C'est le principal risque de la chaîne.
- Si le harnais ne tient pas, le plan de repli garde tout le reste (tickets, tests de contrat, relecteur) et remplace seulement l'outil qui pilote Qwen. Les fichiers `CLAUDE.md` et `docs/EXECUTANT.md` sont du texte simple, lisible par n'importe quel agent de code.

## Déroulé d'une vague

1. Thomas crée la branche `vague-N`.
2. Relecteur, phase A. Il écrit les tests de contrat, fait son commit et pose l'étiquette `vague-N-tests`.
3. Exécutant. `scripts/boucle_executant.py N` ouvre une session par ticket, relance `make check` et fait le commit, jusqu'à ce que tous les tickets soient « fait » ou « bloqué ».
4. Relecteur, phase B. Il vérifie, joue le scénario manuel et écrit `revues/vague-N.md`.
5. Si le verdict est NO-GO, le relecteur écrit des tickets de correction et l'exécutant repart. Deux cycles au plus.
6. Si le verdict est GO, Thomas relit le rapport, essaie lui-même le scénario manuel, fusionne dans `main` et pose l'étiquette `vague-N-ok`.
7. Thomas rapporte le rapport à l'architecte, qui ajuste les tickets de la vague suivante si le relecteur a signalé des défauts répétés.

## Pourquoi le relecteur écrit les tests avant

Un modèle de 27 milliards de paramètres écrit du code correct quand la cible est précise et vérifiable. Des tests écrits à l'avance par un modèle plus fort lui donnent cette cible, et l'empêchent de valider son propre travail avec des tests complaisants. C'est aussi ce qui rend la revue rapide, puisque le relecteur sait déjà ce qu'il cherche.

Le relecteur n'écrit pas de code d'application. S'il le faisait, on ne saurait plus ce que vaut l'exécutant, et la facture en tokens Claude grimperait sans que cela se voie.

## Ce que le relecteur peut voir

Le relecteur est un modèle distant. Tout ce qu'il lit quitte la machine. Il ne voit donc que le dépôt, qui ne contient que du code et des données synthétiques. Il ne faut jamais lui ouvrir `~/Cabinet`, ni lui coller un vrai transcript, une vraie capture d'imputations ou un vrai agenda.

## Sources

- Connexion de Claude Code à une passerelle : https://code.claude.com/docs/en/llm-gateway-connect.md
- Claude Code avec LiteLLM : https://docs.litellm.ai/docs/proxy/client_setup/claude_code
- Fiche de la conversion MLX de Qwen3.8-27B servie par LM Studio : https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-6bit
- Guide Unsloth pour Qwen3.8 : https://unsloth.ai/docs/models/qwen3.8.md
