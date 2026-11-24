#!/usr/bin/env bash
# Vérifie que l'exécutant n'a touché aucun fichier protégé depuis l'étiquette vague-N-tests.
# Usage : scripts/verifier_garde.sh N
set -euo pipefail
N="${1:?numéro de vague manquant}"
TAG="vague-${N}-tests"
PROTEGES=(tests/contrats SPEC.md REVIEW.md CLAUDE.md docs)
CHANGES="$(git diff --name-only "${TAG}"..HEAD -- "${PROTEGES[@]}" || true)"
TICKETS="$(git diff --name-only "${TAG}"..HEAD -- tickets | grep -E '^tickets/V[0-9]+-[0-9]+\.md$' || true)"
if [ -n "${CHANGES}${TICKETS}" ]; then
  echo "NO-GO : fichiers protégés modifiés depuis ${TAG}"
  printf '%s\n' "${CHANGES}" "${TICKETS}" | sed '/^$/d'
  exit 1
fi
echo "OK : aucun fichier protégé modifié depuis ${TAG}"
