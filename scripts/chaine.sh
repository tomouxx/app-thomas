#!/usr/bin/env bash
# Allume, arrête ou contrôle les deux serveurs de la chaîne.
#   scripts/chaine.sh demarrer   serveur du modèle (8080) puis passerelle (4000)
#   scripts/chaine.sh etat       dit ce qui répond
#   scripts/chaine.sh arreter    arrête la passerelle, décharge le modèle, arrête le serveur
# Tout est lié à 127.0.0.1. Voir docs/CHAINE.md et docs/REPRISE.md.
set -euo pipefail

RACINE="$(cd "$(dirname "$0")/.." && pwd)"
MAISON="${CABINET_CHAINE_HOME:-$HOME/.cabinet-chaine}"
LMS="${LMS:-$HOME/.lmstudio/bin/lms}"
LITELLM="${LITELLM:-$MAISON/venv/bin/litellm}"
MODELE="${CABINET_MODELE:-qwen3.8-27b-mlx}"      # clé du modèle dans LM Studio (lms ls)
IDENTIFIANT="qwen3.8-27b"                         # nom servi, repris dans chaine/litellm.config.yaml
CONTEXTE="${CABINET_CONTEXTE:-128000}"
JOURNAUX="$MAISON/journaux"
PID_PASSERELLE="$MAISON/litellm.pid"

repond() { nc -z -G 2 127.0.0.1 "$1" >/dev/null 2>&1; }

etat() {
  repond 8080 && echo "modèle 8080 : répond" || echo "modèle 8080 : muet"
  repond 4000 && echo "passerelle 4000 : répond" || echo "passerelle 4000 : muette"
  "$LMS" ps 2>/dev/null | sed 's/^/  /' || true
}

demarrer() {
  mkdir -p "$JOURNAUX"
  if ! repond 8080; then
    "$LMS" server start --port 8080 --bind 127.0.0.1
  fi
  if ! "$LMS" ps 2>/dev/null | grep -q "$IDENTIFIANT"; then
    "$LMS" load "$MODELE" --identifier "$IDENTIFIANT" --context-length "$CONTEXTE" --gpu max -y
  fi
  if ! repond 4000; then
    nohup "$LITELLM" --config "$RACINE/chaine/litellm.config.yaml" \
      --host 127.0.0.1 --port 4000 >"$JOURNAUX/litellm.log" 2>&1 &
    echo $! >"$PID_PASSERELLE"
    for _ in $(seq 1 60); do repond 4000 && break; sleep 1; done
  fi
  etat
}

arreter() {
  if [ -f "$PID_PASSERELLE" ]; then
    kill "$(cat "$PID_PASSERELLE")" 2>/dev/null || true
    rm -f "$PID_PASSERELLE"
  fi
  "$LMS" unload "$IDENTIFIANT" 2>/dev/null || true
  "$LMS" server stop 2>/dev/null || true
  etat
}

case "${1:-etat}" in
  demarrer) demarrer ;;
  arreter) arreter ;;
  etat) etat ;;
  *) echo "usage : scripts/chaine.sh demarrer|etat|arreter" >&2; exit 2 ;;
esac
