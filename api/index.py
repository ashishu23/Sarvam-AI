"""Vercel ASGI entrypoint; durable Supabase mode is mandatory on Vercel."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
if os.environ.get('VERCEL'):
    os.environ['APP_ENV'] = 'production'
    os.environ['STORAGE_MODE'] = 'supabase'

from app.main import app as app  # noqa: E402
