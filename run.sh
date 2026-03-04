#!/bin/bash
# Lance l'app avec le Python du venv (évite python3 = système)
cd "$(dirname "$0")"
exec venv/bin/python3 app.py
