#!/bin/sh
# Deploy the integration to /config/custom_components on a Samba share (without HACS).
# Service: speaker_id/ (Docker image / add-on), see README.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
CONFIG="${CONFIG:-/Volumes/config}"
[ -d "$CONFIG" ] || { echo "Mount the Home Assistant config share or set CONFIG=/path/to/config"; exit 1; }
# SMB: --inplace, because writing a temporary file and renaming it is sometimes rejected
rsync -a --inplace --delete --exclude '__pycache__' --exclude '.DS_Store' \
    "$HERE/custom_components/speaker_id/" "$CONFIG/custom_components/speaker_id/"
echo "Deployed the integration $(grep '"version"' "$HERE/custom_components/speaker_id/manifest.json" | tr -d ' ,'); restart HA"
