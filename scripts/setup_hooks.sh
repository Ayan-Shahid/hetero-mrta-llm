#!/usr/bin/env bash
# Use the repository's git hooks (.githooks/): Conventional Commit messages, no secrets, tests pass.
set -e
cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/*
echo "git hooks enabled: $(git config core.hooksPath)"
