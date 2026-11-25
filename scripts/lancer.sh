#!/usr/bin/env bash
# Lance les processus de la chaîne comme tâches launchd de la session macOS, pour qu'ils
# survivent à la fermeture du terminal ou de la session Claude qui les a lancés.
#   scripts/lancer.sh passerelle            LiteLLM sur 127.0.0.1:4000 (le modèle doit être chargé)
#   scripts/lancer.sh suivi                 page de suivi sur 127.0.0.1:8770
#   scripts/lancer.sh relecteurs            relecteurs automatiques en veille
#   scripts/lancer.sh boucle 1 [options]    boucle de la vague 1, sous caffeinate
#   scripts/lancer.sh etat                  tâches launchd de la chaîne
#   scripts/lancer.sh arreter <nom>         arrête une tâche (la boucle reçoit SIGTERM et note son arrêt)
# Aucune tâche n'est relancée par launchd quand elle s'arrête (KeepAlive faux).
set -euo pipefail

RACINE="$(cd "$(dirname "$0")/.." && pwd)"
MAISON="${CABINET_CHAINE_HOME:-$HOME/.cabinet-chaine}"
PLISTS="$MAISON/launchd"
DOMAINE="gui/$(id -u)"
PYTHON="$(command -v python3)"

xml() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

tache() {  # tache NOM JOURNAL COMMANDE...
  local nom="$1" journal="$2"; shift 2
  local label="cabinet.chaine.$nom" plist="$PLISTS/cabinet.chaine.$nom.plist" args=""
  mkdir -p "$PLISTS" "$(dirname "$journal")"
  for a in "$@"; do args+="    <string>$(xml "$a")</string>"$'\n'; done
  cat >"$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>
$args  </array>
  <key>WorkingDirectory</key><string>$(xml "$RACINE")</string>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$(xml "$PATH")</string></dict>
  <key>StandardOutPath</key><string>$(xml "$journal")</string>
  <key>StandardErrorPath</key><string>$(xml "$journal")</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>ProcessType</key><string>Interactive</string>
</dict></plist>
EOF
  launchctl bootout "$DOMAINE/$label" 2>/dev/null || true
  launchctl bootstrap "$DOMAINE" "$plist"
  echo "$label lancé, journal $journal"
}

case "${1:-}" in
  passerelle)
    tache passerelle "$MAISON/journaux/litellm.log" /bin/sh -c \
      "LITELLM_MASTER_KEY=\"\$(cat '$MAISON/cle-passerelle')\" exec '$MAISON/venv/bin/litellm' --config '$RACINE/chaine/litellm.config.yaml' --host 127.0.0.1 --port 4000"
    for _ in $(seq 1 60); do nc -z -G 2 127.0.0.1 4000 >/dev/null 2>&1 && break; sleep 1; done
    ;;
  suivi) tache suivi "$RACINE/.chaine/suivi.log" "$PYTHON" scripts/suivi.py ;;
  relecteurs) tache relecteurs "$RACINE/.chaine/revues/veille.log" "$PYTHON" scripts/relecteurs.py ;;
  boucle) shift; tache boucle "$RACINE/.chaine/boucle.log" /usr/bin/caffeinate -i "$PYTHON" scripts/boucle_executant.py "$@" ;;
  etat) launchctl list | awk 'NR == 1 || /cabinet\.chaine\./' ;;
  arreter) launchctl bootout "$DOMAINE/cabinet.chaine.${2:?nom de la tâche}" && echo "cabinet.chaine.$2 arrêté" ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
