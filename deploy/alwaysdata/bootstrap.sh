#!/bin/sh

set -eu

# Keep every file created by this script private by default. The service later
# inherits the same account ownership on alwaysdata Public Cloud.
umask 077

if [ "$(id -u)" -eq 0 ]; then
    echo "Do not install the bridge as root." >&2
    exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
project_dir=$(CDPATH= cd -- "$script_dir/../.." && pwd -P)
cd "$project_dir"

python_bin=${PYTHON_BIN:-python3}
"$python_bin" -m venv .venv
PIP_NO_CACHE_DIR=1 .venv/bin/python -m pip install --disable-pip-version-check --upgrade .

mkdir -p data
chmod 700 data

if [ -L .env ] || { [ -e .env ] && [ ! -f .env ]; }; then
    echo ".env must be a regular file, not a symlink or directory." >&2
    exit 1
fi

if [ ! -e .env ]; then
    cp deploy/alwaysdata/bridge.env.example .env
    echo "Created private .env template; fill it before starting the service."
fi
chmod 600 .env

if [ -e data/max-session.json ]; then
    if [ -L data/max-session.json ] || [ ! -f data/max-session.json ]; then
        echo "data/max-session.json must be a regular file." >&2
        exit 1
    fi
    chmod 600 data/max-session.json
fi

.venv/bin/max-to-telegram --version
printf '%s\n' \
    "Bootstrap complete." \
    "Working directory: $project_dir" \
    "Service command: .venv/bin/max-to-telegram"
