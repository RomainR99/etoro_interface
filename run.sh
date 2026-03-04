#!/bin/bash
# Lance l'app avec le Python du venv (évite python3 = système)
cd "$(dirname "$0")"
# Ton venv a des binaires nommés "python3 2", "python3.12 2" (espace + 2)
if [ -x "venv/bin/python3" ]; then
  exec venv/bin/python3 app.py
elif [ -x "venv/bin/python3.12 2" ]; then
  exec venv/bin/python3.12\ 2 app.py
else
  exec venv/bin/python3\ 2 app.py
fi
