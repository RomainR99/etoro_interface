#!/bin/bash
# Gunicorn du venv uniquement (évite `gunicorn` = binaire Python système si PATH mal ordonné)
cd "$(dirname "$0")"
exec venv/bin/gunicorn -w 4 -b 0.0.0.0:8000 app:app
