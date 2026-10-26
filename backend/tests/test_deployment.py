import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, app_env='test', local_data_dir=tmp_path, sarvam_api_key='')
    with TestClient(create_app(settings)) as client:
        yield client


def session(client):
    response = client.post('/api/auth/local')
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['access_token']}


def test_private_case_lifecycle_and_two_user_isolation(client):
    first, second = session(client), session(client)
    created = client.post('/api/cases', headers=first, json={'title': 'Fictional deployment check', 'consent': True})
    assert created.status_code == 201
    path = '/api/cases/' + created.json()['id']
    assert client.get(path, headers=first).status_code == 200
    assert client.get(path, headers=second).status_code == 404
    assert client.delete(path, headers=second).status_code == 404
    assert client.get(path).status_code == 401
    assert client.delete(path, headers=first).json()['status'] == 'complete'
    assert client.get(path, headers=first).status_code == 404
    assert client.get('/api/cases', headers=first).json() == []


def test_private_response_no_cache(client):
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'


def test_limits_and_public_configuration(client):
    config = client.get('/api/capabilities').json()
    assert config['max_upload_mb'] == config['max_upload_batch_mb'] == 3
    assert 'sarvam_api_key' not in config
    assert config['supabase_publishable_key'] is None
    assert client.post('/api/cases', content=b'x' * 4_000_001).status_code == 413


def test_production_rejects_local_persistence():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env='production', storage_mode='local')


def test_production_rejects_elevated_public_key():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, storage_mode='supabase', supabase_url='https://example.supabase.co', supabase_publishable_key='sb_secret_for_test')


def test_vercel_routes_and_ignored_private_data():
    root = Path(__file__).resolve().parents[2]
    config = json.loads((root / 'vercel.json').read_text())
    assert config['outputDirectory'] == 'frontend/dist'
    assert config['functions']['api/index.py']['maxDuration'] == 300
    assert config['rewrites'][0]['source'] == '/api/:path*'
    assert 'private-data/' in (root / '.vercelignore').read_text()
