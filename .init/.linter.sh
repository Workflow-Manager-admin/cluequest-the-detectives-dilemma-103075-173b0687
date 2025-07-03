#!/bin/bash
cd /home/kavia/workspace/code-generation/cluequest-the-detectives-dilemma-103075-173b0687/backend_api
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

