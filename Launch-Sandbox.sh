#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
    printf '\033[31muv is not installed or is not on PATH.\033[0m\n' >&2
    printf 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ and try again.\n' >&2
    exit 1
fi

printf '\033[36mPreparing Sandbox...\033[0m\n'
uv sync

printf '\033[32mOpening Sandbox Job Wizard...\033[0m\n'
uv run streamlit run app.py
