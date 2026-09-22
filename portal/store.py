"""SQLite store for portal accounts, sessions, and reservations.

Reservations stay advisory: nothing here touches GPU permissions or processes.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .accounts import (SESSION_DAYS, hash_password, hash_token, new_session_token,
                       normalize_username, verify_password)
from .demo import now as _now

DEMO_PASSWORD = 'demo-password'


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, '
                       'password_hash TEXT NOT NULL, display_name TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, '
                       'created_at TEXT NOT NULL, expires_at TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    # --- accounts -----------------------------------------------------------
    @staticmethod
    def _user(row):
        return {'id': row[0], 'username': row[1], 'display_name': row[3], 'role': row[4], 'created_at': row[5]}

    def count_users(self):
        with self.connect() as db:
            return db.execute('SELECT count(*) FROM users').fetchone()[0]

    def list_users(self):
        with self.connect() as db:
            return [self._user(row) for row in db.execute('SELECT * FROM users ORDER BY created_at')]

    def create_user(self, username, password, display_name, role=None):
        username = normalize_username(username)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                raise ValueError('이미 사용 중인 아이디입니다.')
            # The first account administers the lab; later accounts are ordinary members.
            role = role or ('admin' if db.execute('SELECT count(*) FROM users').fetchone()[0] == 0 else 'member')
            row = (uuid.uuid4().hex, username, hash_password(password), display_name.strip(), role, _now().isoformat())
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?)', row)
        return self._user(row)

    def authenticate(self, username, password):
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (normalize_username(username),)).fetchone()
        if not row or not verify_password(row[2], password):
            return None
        return self._user(row)

    def change_password(self, user_id, password):
        with self.connect() as db:
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (hash_password(password), user_id))
            db.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))  # Other devices must sign in again.

    # --- sessions -----------------------------------------------------------
    def start_session(self, user_id):
        token = new_session_token()
        started = _now()
        with self.connect() as db:
            db.execute('DELETE FROM sessions WHERE expires_at < ?', (started.isoformat(),))
            db.execute('INSERT INTO sessions VALUES (?,?,?,?)', (hash_token(token), user_id, started.isoformat(),
                                                                 (started+timedelta(days=SESSION_DAYS)).isoformat()))
        return token

    def resolve_session(self, token):
        if not token:
            return None
        with self.connect() as db:
            row = db.execute('SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id '
                             'WHERE sessions.token_hash=? AND sessions.expires_at > ?',
                             (hash_token(token), _now().isoformat())).fetchone()
        return self._user(row) if row else None

    def end_session(self, token):
        if token:
            with self.connect() as db:
                db.execute('DELETE FROM sessions WHERE token_hash=?', (hash_token(token),))

    # --- reservations -------------------------------------------------------
    def list(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM reservations')]

    def save(self, data, user, reservation_id=None):
        """Check and write under one lock, so multi-GPU reservations are atomic."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = [json.loads(row[0]) for row in db.execute('SELECT payload FROM reservations')]
            if reservation_id:
                old = next((r for r in rows if r['id'] == reservation_id), None)
                if old is None:
                    raise LookupError('예약을 찾을 수 없습니다.')
                if not self.may_manage(old, user):
                    raise PermissionError('본인 예약만 수정할 수 있습니다.')
            start, end = datetime.fromisoformat(data['start']), datetime.fromisoformat(data['end'])
            for row in rows:
                if row['id'] != reservation_id and set(row['resources']) & set(data['resources']):
                    if start < datetime.fromisoformat(row['end']) and end > datetime.fromisoformat(row['start']):
                        raise ValueError('선택한 GPU에 겹치는 예약이 있습니다. 다른 시간 또는 GPU를 선택하세요.')
            owner = data.get('owner_linux') if reservation_id and data.get('owner_linux') else user['username']
            name = data.get('owner_name') if reservation_id and data.get('owner_name') else user['display_name']
            data.update(id=reservation_id or uuid.uuid4().hex, owner_linux=owner, owner_name=name, contact=owner)
            db.execute('INSERT OR REPLACE INTO reservations VALUES (?,?)', (data['id'], json.dumps(data)))
        return data

    def delete(self, reservation_id, user):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM reservations WHERE id=?', (reservation_id,)).fetchone()
            if not row:
                raise LookupError('예약을 찾을 수 없습니다.')
            if not self.may_manage(json.loads(row[0]), user):
                raise PermissionError('본인 예약만 취소할 수 있습니다.')
            db.execute('DELETE FROM reservations WHERE id=?', (reservation_id,))

    @staticmethod
    def may_manage(reservation, user):
        return bool(user) and (user['role'] == 'admin' or reservation.get('owner_linux') == user['username'])

    # --- demo seed ----------------------------------------------------------
    def seed_demo(self, samples, members):
        with self.connect() as db:
            if db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                return
        for username, display_name in members:
            try:
                self.create_user(username, DEMO_PASSWORD, display_name, role='admin' if username == 'student1' else 'member')
            except ValueError:
                pass
        base = _now().replace(minute=0, second=0, microsecond=0)
        names = dict(members)
        with self.connect() as db:
            for title, project, owner, ids, start, end in samples:
                row = dict(id=uuid.uuid4().hex, title=title, project=project, owner_linux=owner, owner_name=names[owner],
                           resources=ids, start=(base+timedelta(hours=start)).isoformat(), end=(base+timedelta(hours=end)).isoformat(),
                           cpu_cores=16, ram_gb=64, job_type='Training', note='', contact=owner)
                db.execute('INSERT INTO reservations VALUES (?,?)', (row['id'], json.dumps(row)))
            db.execute("INSERT INTO metadata VALUES ('seeded')")
