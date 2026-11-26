# Questions de la phase A, vague 2

Relevées le 26.11.2026 en écrivant les tests de contrat de V2-C1 et de V2-01 à V2-09. Aucune ne bloque la vague. Chaque ligne dit ce que les tests font en attendant une réponse. Les tests ont été éprouvés contre trois implémentations jetables, écrites hors du dépôt et jamais livrées. Avec elles, tous les contrats passent, et des erreurs plantées exprès les font tomber.

## Réglé pendant la phase A

| Sujet | Décision |
| --- | --- |
| Cinq contrats V1-08 figeaient la liste exacte `["ingestion", "agenda"]` du pipeline et du journal. Ils auraient bloqué V2-01 dès l'ajout de l'agent `transcripts` | Ils vérifient maintenant les deux agents du socle et leur ordre, quelle que soit la suite. Le scénario S1 de `specs/09 §4` est reformulé de même |
| Les nouveaux tests s'appellent `test_contract_*`, alors que `specs/09 §2` écrit `test_contrat_*` | Les identifiants des contrats passent en anglais, comme ceux qu'on exige de l'exécutant (`CLAUDE.md`, règle 6). Les contrats des vagues 0 et 1 gardent leurs noms. La page de suivi retire les deux préfixes |

## Ouvertes

| # | Ticket | Question | Section | Ce que font les tests |
| --- | --- | --- | --- | --- |
| Q1 | V2-01 | La forme d'un `.docx` Teams reste une hypothèse, faute d'exemple anonymisé. Une ligne de texte libre qui finit par une heure (« Reprise à 14:30 ») serait prise pour un en-tête d'orateur | `specs/00 §4.13`, `specs/06 §1.2` | `.docx` synthétiques au format supposé, texte libre sans ligne de ce genre |
| Q2 | V2-01 | Dans « Fichier introuvable : {nom}. », `{nom}` est-il le nom du fichier ou son chemin relatif ? | `specs/06 §2`, étape 2 | début du message et présence du nom |
| Q3 | V2-01, V2-02 | « Une ligne par segment » ne dit pas si le texte finit par un retour à la ligne | `specs/06 §1` | comparaison par `splitlines()` |
| Q4 | V2-03 | Le `FakeTranscriber` de `specs/09 §3.2` a `"fr"` par défaut et ne peut pas montrer que l'agent transmet la langue | `specs/09 §3.2`, `specs/06 §3.2` | une variante sans valeur par défaut exige `"fr"` explicite |
| Q5 | V2-03 | Sans consentement, rien n'est prévu pour `item.error` ni pour `consent_at` | `specs/06 §3.2`, étape 2 | ni l'un ni l'autre n'est vérifié |
| Q6 | V2-03 | « Si l'import échoue » : les tests simulent la bibliothèque absente par `sys.modules["mlx_whisper"] = None`, qui lève `ImportError` et non `ModuleNotFoundError` | `specs/06 §3.1` | une implémentation qui ne rattrape que `ModuleNotFoundError` échoue |
| Q7 | V2-04 | `ValidationBlocked` est défini en `specs/06 §8.2` (V2-06), mais `regenerate` (V2-04) doit déjà le lever | `specs/06 §4.4` et `§8.2` | `cabinet.agents.minutes.ValidationBlocked` exigé dès V2-04, message exact |
| Q8 | V2-04 | Le ticket écrit `MinutesAgent(llm)`, la spec `MinutesAgent(llm, context_tokens=64000)` | `specs/06 §4` | la spec. V2-04 construit avec `llm` seul, V2-09 passe aussi `context_tokens` |
| Q9 | V2-04 | La forme exacte de l'intertitre de `meeting_source` n'est pas écrite | `specs/06 §4.2` | `## Source n : libellé`, retour à la ligne, texte, ligne vide entre les blocs |
| Q10 | V2-04 | Qui valide la session après `regenerate` ? Quel nom de modèle dans `AgentOutcome` quand tous les appels échouent ? | `specs/06 §4.4`, `specs/05 §2.3` | `regenerate` appelé dans `record_run`. Le nom du modèle n'est pas vérifié dans ce cas |
| Q11 | V2-05 | Le pas des fenêtres (un quart de la longueur) rend le score sensible à la position. Une phrase de 20 mots à deux mots changés score entre 0,83 et 0,93 selon sa place, et peut être marquée « à vérifier » à tort | `specs/06 §5`, étape 4 | l'extrait du critère 2 est placé en tête de source. L'architecte juge si ce pas suffit |
| Q12 | V2-05 | Le critère 4 (moins de 2 s) ne dit pas quel extrait chronométrer. Un extrait inventé a pris 1,2 s sur la référence | `specs/06 §5` | extrait à deux mots changés, au milieu de la source |
| Q13 | V2-05 | « Ponctuation » n'est pas défini | `specs/06 §5` | catégories Unicode qui commencent par P, signes courants seulement |
| Q14 | V2-09 | Le ticket dit « les 200 derniers mots », la spec « les dernières lignes entières, 200 mots au plus ». Pour une ligne plus longue que la limite, le recouvrement la précède-t-il ? | `specs/06 §6`, étapes 3 et 4 | la spec. La ligne trop longue reste entière, les autres morceaux respectent la limite |
| Q15 | V2-09 | Le texte de `build_summary_messages` et le nombre de paires de balises ne sont pas fixés | `specs/06 §6`, étape 3 | phrase de garde, « 120 » dans le message système, chaque résumé entre balises, `</source>` échappé |
| Q16 | V2-06 | Les bornes −30 et +14 jours de `GET /reunions` sont-elles incluses ? | `specs/08 §4.1` | −29 et +13 dedans, −31 et +15 dehors |
| Q17 | V2-06 | Quels types d'arguments reçoit `render_minutes` ? Quel format pour le nombre de segments et le numéro de version ? | `specs/06 §7`, `specs/08 §4.2` | objets de `cabinet.db.models`. « Version 1 » ou « v1 » acceptés |
| Q18 | V2-06, V2-08 | Comment les routes `regenerer` et `fiche/regenerer` obtiennent-elles leur modèle ? | `specs/08 §4.3`, `§4.4` | `LocalLLM` construit depuis les réglages, `complete` remplacé par la doublure |
| Q19 | V2-07 | Vers quelle adresse redirigent les routes de suivi ? Une réunion validée de date future compte-t-elle parmi les trois dernières ? | `specs/08 §5.1`, `§5.2` | code 303 seulement. Réunions passées seulement |
| Q20 | V2-08 | Forme d'une action sans responsable ou sans échéance, ordre des décisions récentes, jonction de plusieurs notes, fiche après une `LLMError` comptée comme traitée ou non | `specs/06 §9.1`, `§9.2`, `§9.4` | forme complète mot pour mot, décisions comparées comme un ensemble, une seule note, `errors == 2` seulement |

Aucune de ces questions ne touche un principe de `specs/00 §5`. Les réponses peuvent attendre la revue de la vague 2.
