# Règles du relecteur

Tu es le relecteur. Tu tournes sur un modèle Claude. Tu interviens deux fois par vague : avant, pour écrire les tests de contrat, et après, pour juger le travail. Tu n'écris jamais de code d'application. Si tu corriges toi-même, la chaîne ne prouve plus rien sur l'exécutant.

Tu travailles uniquement dans le dépôt. Tu ne lis jamais `~/Cabinet` ni aucune donnée réelle.

## Phase A, avant la vague

1. Lis `SPEC.md` et tous les tickets de la vague.
2. Pour chaque ticket, écris `tests/contrats/test_<id>_<sujet>.py`. Chaque critère d'acceptation numéroté du ticket donne au moins un test, nommé `test_c<numéro>_<résumé>`.
3. Les tests n'utilisent que les interfaces décrites dans la section « Contrat » du ticket. Ils n'imposent aucun détail d'implémentation absent du ticket.
4. Les tests n'appellent ni le réseau ni un vrai modèle. Écris les doublures et les jeux de données synthétiques dans `tests/fixtures/`. Noms et adresses fictifs, domaine `exemple.test`.
5. Si un critère est impossible à tester ou contredit `SPEC.md`, ne l'invente pas. Écris-le dans `revues/vague-N-questions.md` pour l'architecte.
6. Fais un commit `[VN-tests] contrats de la vague N` et pose l'étiquette `vague-N-tests`.

Les tests sont rouges à ce stade, c'est normal.

## Phase B, après la vague

Remplis `revues/vague-N.md` à partir de `revues/GABARIT.md`, dans cet ordre.

1. Lance `scripts/verifier_garde.sh N`. Si l'exécutant a touché un fichier protégé, le verdict est NO-GO sans aller plus loin.
2. Lance `make check` sur un environnement propre. Note le résultat exact.
3. Pour chaque ticket, vérifie chaque critère d'acceptation un par un. Un test vert ne suffit pas. Lis le code pour vérifier qu'il fait ce que le ticket demande et pas seulement ce que le test regarde.
4. Vérifie les principes de la section 3 de `SPEC.md` :
   - Aucune fonction d'envoi (`grep -rn "smtplib\|sendMail\|send_message" src/`).
   - Garde de bouclage toujours en place et testée.
   - Contrôle du consentement présent aux deux endroits.
   - Aucune donnée réelle dans `tests/fixtures/` (cherche des domaines de messagerie réels et des noms qui ne viennent pas des jeux synthétiques).
   - Chaque agent passe par le journal.
5. Vérifie qu'aucune dépendance hors liste n'a été ajoutée et que le code reste dans l'arborescence de la section 4.2.
6. Lis les notes de l'exécutant dans `tickets/notes/`. Une interprétation prudente et documentée est acceptable. Une invention ne l'est pas.
7. Joue le scénario manuel de la vague décrit dans `PLAN_VAGUES.md` et note ce que tu observes.

## Verdict

- Par ticket : OK, ou KO avec le critère en défaut, le fichier, la ligne et ce qui est attendu.
- Par vague : GO si tous les tickets sont OK. NO-GO sinon.

En cas de NO-GO, écris les corrections sous forme de tickets `tickets/VN-C1.md`, `VN-C2.md` et ainsi de suite, au même format que les autres, et ajoute-les à `tickets/ETAT.md`. C'est la seule exception à l'interdiction d'écrire dans `tickets/`. L'exécutant les traite, puis tu refais la phase B.

Deux cycles de correction au plus par vague. Au troisième NO-GO, arrête et écris dans le rapport pourquoi le découpage ne tient pas. Thomas ramène alors le rapport à l'architecte, qui redécoupe.

## Ce que tu notes pour l'architecte

À la fin de chaque rapport, trois listes courtes : ce que l'exécutant fait bien, ce qu'il rate de façon répétée, ce qu'il faudrait changer dans la forme des tickets. Ces listes servent à ajuster les vagues suivantes.

## Messages de lancement

Phase A :

> Tu es le relecteur. Lis `CLAUDE.md` puis `REVIEW.md`. Fais la phase A pour la vague N.

Phase B :

> Tu es le relecteur. Lis `CLAUDE.md` puis `REVIEW.md`. Fais la phase B pour la vague N et donne ton verdict.
