#!/bin/sh
# Tests of the service (own environment, no HA) and of the integration (pytest-homeassistant).
#   python3.13 -m venv .venv-service && .venv-service/bin/pip install -r speaker_id/requirements.txt pytest pytest-asyncio ruff
#   python3.13 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
set -e
cd "$(dirname "$0")/.."
.venv-service/bin/python -m pytest -q -p no:cacheprovider tests_service
.venv/bin/python -m pytest -q -p no:cacheprovider tests
.venv/bin/ruff check . --exclude .venv --exclude .venv-service
.venv/bin/ruff format --check . --exclude .venv --exclude .venv-service
