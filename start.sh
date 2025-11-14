#!/usr/bin/env bash
set -e

# Ensure data directory exists (Render persistent disk mounted here if attached)
#mkdir -p /data

# Run FastAPI
exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
