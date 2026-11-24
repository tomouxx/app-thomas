#!/usr/bin/env bash
# Ouvre une session interactive de Claude Code reliée au modèle local par la passerelle.
# Sert au diagnostic. Une vague se traite avec scripts/boucle_executant.py.
# À lancer dans un terminal dédié. Ne jamais exporter ces variables dans le terminal du relecteur.
set -euo pipefail
export ANTHROPIC_BASE_URL="http://127.0.0.1:4000"
export ANTHROPIC_AUTH_TOKEN="$(cat "$HOME/.cabinet-chaine/cle-passerelle")"   # clé locale créée par scripts/chaine.sh
export ANTHROPIC_MODEL="qwen-local"
export ANTHROPIC_SMALL_FAST_MODEL="qwen-local"
export ANTHROPIC_DEFAULT_HAIKU_MODEL="qwen-local"
export CLAUDE_CONFIG_DIR="$HOME/.cabinet-chaine/claude-executant"
unset ANTHROPIC_API_KEY || true
exec claude "$@"
