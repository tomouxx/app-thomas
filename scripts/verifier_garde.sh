#!/usr/bin/env bash
# Vérifie que l'exécutant n'a touché aucun fichier protégé depuis l'étiquette vague-N-tests.
# Seuls les commits de ticket comptent (`[V1-03] …`, `[V1-C1] …`). Les commits `[VN-chaine]`,
# `[VN-etat]` et `[VN-tests]` viennent du relecteur ou de la boucle et peuvent toucher docs/ ou scripts/.
# Usage : scripts/verifier_garde.sh N
set -euo pipefail
N="${1:?numéro de vague manquant}"
TAG="vague-${N}-tests"
MOTIF='^(tests/contrats/|specs/|SPEC\.md$|REVIEW\.md$|CLAUDE\.md$|docs/|scripts/|chaine/|tickets/V[0-9]+-C?[0-9]+\.md$)'
CHANGES=""
for c in $(git log --format=%H "${TAG}"..HEAD); do
  sujet="$(git log -1 --format=%s "$c")"
  [[ "$sujet" =~ ^\[V[0-9]+-C?[0-9]+\] ]] || continue
  fichiers="$(git show --name-only --format= "$c" | grep -E "$MOTIF" || true)"
  [ -n "$fichiers" ] && CHANGES+="$(git log -1 --format=%h "$c") ${sujet}"$'\n'"$(sed 's/^/  /' <<<"$fichiers")"$'\n'
done
if [ -n "$CHANGES" ]; then
  echo "NO-GO : fichiers protégés modifiés par un commit de ticket depuis ${TAG}"
  printf '%s' "$CHANGES"
  exit 1
fi
echo "OK : aucun commit de ticket ne modifie un fichier protégé depuis ${TAG}"
