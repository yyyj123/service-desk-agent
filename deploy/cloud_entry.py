"""Cloud evaluation entrypoint; local disk is NOT a durable storage contract.

Cloud environment secrets must be provisioned before importing this module.
Bootstrap only the bundled demo knowledge, never local runtime/customer data.
"""
import os
import uuid

from service.config import ROOT, Settings
from service.store import Store
from service.security import hasher
from service.retrieval import KnowledgeBase
from service.api import create_app

settings = Settings()
settings.validate()
if not settings.database_url:
    raise RuntimeError('Cloud deployment requires DATABASE_URL; connect PostgreSQL before deploying.')
store = Store(settings.database_url or settings.data_dir / 'state.db', settings.jwt_secret)
accounts = []
with store.connection(True) as db:
    for username, role in [('employee', 'employee'), ('admin', 'admin')]:
        password = os.environ[f'CLOUD_DEMO_{username.upper()}_PASSWORD']
        if not db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',
                       (uuid.uuid4().hex, 'demo', username, hasher.hash(password), role))
        accounts.append(f'{username} ({role}): {password}')
(settings.data_dir / 'accounts.txt').write_text('\n'.join(accounts) + '\n', encoding='utf-8')
kb = KnowledgeBase(settings)
if not kb.collection.count():
    kb.ingest(ROOT / 'knowledge')
app = create_app(settings=settings, kb=kb)
