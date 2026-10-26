"""Owner-scoped persistence. Local SQLite is explicitly development-only."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException

from .config import Settings


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return str(uuid4())


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.storage_mode == 'local':
            settings.local_data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = settings.local_data_dir / 'eagle-eye.sqlite3'
            with self.db() as db:
                db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, owner TEXT NOT NULL, version INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS cases_owner ON cases(owner);
                CREATE TABLE IF NOT EXISTS quotas(owner TEXT, bucket TEXT, used INTEGER NOT NULL, PRIMARY KEY(owner,bucket));
                ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def local_session(self) -> dict:
        token = secrets.token_urlsafe(48)
        owner = uid()
        with self.db() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), owner, time.time() + 30 * 86400))
        return {'access_token': token, 'user_id': owner, 'expires_in': 30 * 86400, 'mode': 'local'}

    async def request(self, method: str, path: str, token: str, **kwargs) -> httpx.Response:
        headers = {'apikey': self.settings.supabase_publishable_key, 'Authorization': f'Bearer {token}'}
        headers.update(kwargs.pop('headers', {}))
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.request(method, self.settings.supabase_url.rstrip('/') + path, headers=headers, **kwargs)
        except httpx.HTTPError:
            raise HTTPException(503, 'Private storage is unavailable. Your last saved case is preserved.') from None
        if response.is_error:
            code = response.status_code
            if code in (401, 403):
                raise HTTPException(401, 'Sign in again to access your private case.')
            if code == 404:
                raise HTTPException(404, 'Record not found or access denied.')
            # RPC uses serialization_failure for optimistic concurrency.
            if code == 409 or (response.headers.get('content-type', '').startswith('application/json') and response.json().get('code') == '40001'):
                raise HTTPException(409, 'The case changed in another request. Reload and review before retrying.')
            raise HTTPException(503, 'Private storage rejected this operation. Check the migration and configuration.')
        return response

    async def authenticate(self, token: str) -> str:
        if not token or len(token) > 8192:
            raise HTTPException(401, 'A private session is required.')
        if self.settings.storage_mode == 'local':
            with self.db() as db:
                row = db.execute('SELECT owner FROM sessions WHERE token_hash=? AND expires>?', (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if row is None:
                raise HTTPException(401, 'Session expired. Start a new local session.')
            return row['owner']
        response = await self.request('GET', '/auth/v1/user', token)
        owner = response.json().get('id')
        if not owner:
            raise HTTPException(401, 'Invalid session.')
        return owner

    async def list(self, owner: str, token: str) -> list[dict]:
        if self.settings.storage_mode == 'local':
            with self.db() as db:
                rows = db.execute('SELECT payload FROM cases WHERE owner=? AND deleted=0', (owner,)).fetchall()
            cases = [json.loads(row['payload']) for row in rows]
        else:
            response = await self.request('GET', '/rest/v1/cases', token, params={'owner_id': f'eq.{owner}', 'deleted': 'eq.false', 'select': 'payload'})
            cases = [row['payload'] for row in response.json()]
        return sorted(cases, key=lambda case: case['updated_at'], reverse=True)

    async def get(self, case_id: str, owner: str, token: str, allow_deleted: bool = False) -> dict:
        if self.settings.storage_mode == 'local':
            with self.db() as db:
                row = db.execute('SELECT payload,deleted FROM cases WHERE id=? AND owner=?', (case_id, owner)).fetchone()
            if row is None or (row['deleted'] and not allow_deleted):
                raise HTTPException(404, 'Case not found or access denied.')
            return json.loads(row['payload'])
        response = await self.request('GET', '/rest/v1/cases', token, params={'id': f'eq.{case_id}', 'owner_id': f'eq.{owner}', 'select': 'payload,deleted'})
        rows = response.json()
        if not rows or (rows[0]['deleted'] and not allow_deleted):
            raise HTTPException(404, 'Case not found or access denied.')
        return rows[0]['payload']

    async def create(self, case: dict, owner: str, token: str):
        if self.settings.storage_mode == 'local':
            with self.db() as db:
                db.execute('INSERT INTO cases VALUES(?,?,?,?,?)', (case['id'], owner, 1, 0, json.dumps(case, ensure_ascii=False)))
        else:
            await self.request('POST', '/rest/v1/cases', token, json={'id': case['id'], 'owner_id': owner, 'version': 1, 'payload': case})

    async def save(self, case: dict, owner: str, token: str, allow_deleted: bool = False):
        if len(json.dumps(case, ensure_ascii=False).encode('utf-8')) > 2_800_000:
            raise HTTPException(422, 'This case has reached its storage limit. Export it and start a new case for further material.')
        expected = case['storage_version']
        case['updated_at'] = now()
        case['storage_version'] = expected + 1
        deleted = case.get('deletion_status') in ('pending', 'complete')
        if self.settings.storage_mode == 'local':
            with self.db() as db:
                cursor = db.execute('UPDATE cases SET payload=?,version=?,deleted=? WHERE id=? AND owner=? AND version=? AND (deleted=0 OR ?)',
                                    (json.dumps(case, ensure_ascii=False), expected + 1, int(deleted), case['id'], owner, expected, int(allow_deleted)))
                if cursor.rowcount != 1:
                    case['storage_version'] = expected
                    raise HTTPException(409, 'The case changed or was deleted. Reload before retrying.')
        else:
            await self.request('POST', '/rest/v1/rpc/save_case', token, json={'p_id': case['id'], 'p_expected': expected, 'p_payload': case, 'p_deleted': deleted, 'p_cleanup': allow_deleted})

    async def consume(self, owner: str, token: str, category: str, amount: int, limit: int, seconds: int):
        bucket = f'{category}:{int(time.time()) // seconds}'
        if self.settings.storage_mode == 'local':
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT used FROM quotas WHERE owner=? AND bucket=?', (owner, bucket)).fetchone()
                used = row['used'] if row else 0
                if used + amount > limit:
                    raise HTTPException(429, 'Usage limit reached. Saved cases and text editing remain available.')
                db.execute('INSERT INTO quotas VALUES(?,?,?) ON CONFLICT(owner,bucket) DO UPDATE SET used=excluded.used', (owner, bucket, used + amount))
        else:
            response = await self.request('POST', '/rest/v1/rpc/consume_quota', token, json={'p_bucket': bucket, 'p_amount': amount, 'p_limit': limit})
            if not response.json():
                raise HTTPException(429, 'Usage limit reached. Try again in the next quota window.')

    def local_path(self, path: str) -> Path:
        root = (self.settings.local_data_dir / 'objects').resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root) or target == root:
            raise HTTPException(400, 'Invalid object path.')
        return target

    async def put_file(self, path: str, data: bytes, mime: str, token: str):
        if self.settings.storage_mode == 'local':
            target = self.local_path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        else:
            await self.request('POST', f'/storage/v1/object/{self.settings.supabase_storage_bucket}/{quote(path)}', token, content=data, headers={'Content-Type': mime, 'x-upsert': 'false'})

    async def get_file(self, path: str, token: str) -> bytes:
        if self.settings.storage_mode == 'local':
            target = self.local_path(path)
            if not target.is_file():
                raise HTTPException(404, 'Original file is unavailable.')
            return target.read_bytes()
        response = await self.request('GET', f'/storage/v1/object/authenticated/{self.settings.supabase_storage_bucket}/{quote(path)}', token)
        return response.content

    async def delete_files(self, paths: list[str], token: str):
        if not paths:
            return
        if self.settings.storage_mode == 'local':
            for path in paths:
                self.local_path(path).unlink(missing_ok=True)
        else:
            await self.request('DELETE', f'/storage/v1/object/{self.settings.supabase_storage_bucket}', token, json={'prefixes': paths})
