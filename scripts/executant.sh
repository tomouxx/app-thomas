#!/usr/bin/env bash
# Lance Claude Code relié au modèle local par la passerelle.
# À lancer dans un terminal dédié. Ne jamais exporter ces variables dans le terminal du relecteur.
set -euo pipefail
export ANTHROPIC_BASE_URL="http://127.0.0.1:4000"
export ANTHROPIC_AUTH_TOKEN="jeton-factice-local"   # valeur sans secret, la passerelle est locale
export ANTHROPIC_MODEL="qwen-local"
unset ANTHROPIC_API_KEY || true
exec claude "$@"
