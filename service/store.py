"""Transactional single-host state. Scope all records by authenticated user/tenant."""
import hashlib
import hmac
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

class Store:
    def __init__(self, path, secret):
        self.path, self.secret = str(path), secret.encode()
        self.is_postgres = self.path.startswith(("postgres://", "postgresql://"))
        schema = '''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, tenant TEXT NOT NULL, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(jti TEXT PRIMARY KEY,user_id TEXT NOT NULL,expires REAL NOT NULL,csrf TEXT NOT NULL,revoked INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS rate_events(bucket TEXT NOT NULL,ts REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS rate_idx ON rate_events(bucket,ts);
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,tenant TEXT NOT NULL,updated REAL NOT NULL,busy_until REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,conversation_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS requests(user_id TEXT NOT NULL,request_id TEXT NOT NULL,body_hash TEXT NOT NULL,status TEXT NOT NULL,response TEXT,created REAL NOT NULL,PRIMARY KEY(user_id,request_id));
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,user_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL,expires REAL NOT NULL,result TEXT,reviewer TEXT);
            CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,ts REAL NOT NULL,tenant TEXT NOT NULL,actor TEXT NOT NULL,event TEXT NOT NULL,target TEXT NOT NULL,detail TEXT NOT NULL,prev TEXT NOT NULL,digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS grants(tenant TEXT,user_id TEXT,resource TEXT,created REAL,PRIMARY KEY(tenant,user_id,resource));
            CREATE TABLE IF NOT EXISTS answer_details(message_id INTEGER PRIMARY KEY, detail TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS feedback(message_id INTEGER,user_id TEXT,rating INTEGER,comment TEXT,updated REAL,PRIMARY KEY(message_id,user_id));
            CREATE TABLE IF NOT EXISTS chat_stats(message_id INTEGER PRIMARY KEY,tenant TEXT,user_id TEXT,question TEXT,mode TEXT,latency REAL,created REAL);
            CREATE TABLE IF NOT EXISTS knowledge_docs(id TEXT PRIMARY KEY,tenant TEXT,title TEXT,source TEXT,visibility TEXT,revision INTEGER,chunks INTEGER,updated REAL,content TEXT);
            CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id,id);
            CREATE INDEX IF NOT EXISTS conversations_owner ON conversations(user_id,tenant,updated);
            CREATE INDEX IF NOT EXISTS requests_active ON requests(status,created);
            CREATE INDEX IF NOT EXISTS actions_owner ON actions(tenant,user_id,status,expires);
            CREATE INDEX IF NOT EXISTS stats_tenant_created ON chat_stats(tenant,created);
            CREATE INDEX IF NOT EXISTS rate_cleanup ON rate_events(ts);
            '''
        if self.is_postgres:
            schema = schema.replace('PRAGMA journal_mode=WAL;', '').replace(' REAL', ' DOUBLE PRECISION')
            schema = schema.replace('messages(id INTEGER PRIMARY KEY', 'messages(id BIGSERIAL PRIMARY KEY')
            schema = schema.replace('audit(seq INTEGER PRIMARY KEY', 'audit(seq BIGSERIAL PRIMARY KEY')
        with self.connection(True) as db:
            db.executescript(schema)

    @contextmanager
    def connection(self, write=False):
        if self.is_postgres:
            from .postgres import transaction
            with transaction(self.path,write) as db:
                yield db
            return
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA busy_timeout=15000')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def audit(self, db, actor, event, target='', detail=None):
        now = time.time()
        prev = db.execute('SELECT digest FROM audit ORDER BY seq DESC LIMIT 1').fetchone()
        prev = prev['digest'] if prev else 'GENESIS'
        detail = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
        parts = [now, actor['tenant'], actor['id'], event, target, detail, prev]
        digest = hmac.new(self.secret, json.dumps(parts, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
        db.execute('INSERT INTO audit(ts,tenant,actor,event,target,detail,prev,digest) VALUES(?,?,?,?,?,?,?,?)', (*parts, digest))

    def verify_audit(self):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM audit ORDER BY seq').fetchall()
        prev = 'GENESIS'
        for row in rows:
            parts = [row[k] for k in ('ts','tenant','actor','event','target','detail','prev')]
            digest = hmac.new(self.secret, json.dumps(parts, ensure_ascii=False).encode(), hashlib.sha256).hexdigest()
            if row['prev'] != prev or not hmac.compare_digest(digest, row['digest']):
                return False
            prev = digest
        return True

    def limit(self, bucket, limit, window=60):
        now = time.time()
        with self.connection(True) as db:
            db.execute('DELETE FROM rate_events WHERE ts < ?', (now - 3600,))
            row = db.execute('SELECT COUNT(*) n,MIN(ts) first FROM rate_events WHERE bucket=? AND ts>?', (bucket,now-window)).fetchone()
            if row['n'] >= limit:
                return max(1,int(row['first']+window-now)+1)
            db.execute('INSERT INTO rate_events VALUES(?,?)',(bucket,now))
        return 0

    def propose(self, actor, kind, payload):
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.connection(True) as db:
            old = db.execute("SELECT * FROM actions WHERE user_id=? AND tenant=? AND kind=? AND payload=? AND status='pending' AND expires>?",(actor['id'],actor['tenant'],kind,encoded,time.time())).fetchone()
            if old:
                return dict(old)
            aid = uuid.uuid4().hex
            db.execute('INSERT INTO actions(id,tenant,user_id,kind,payload,status,created,expires) VALUES(?,?,?,?,?,?,?,?)',(aid,actor['tenant'],actor['id'],kind,encoded,'pending',time.time(),time.time()+1800))
            self.audit(db,actor,'action.proposed',aid,{'kind':kind})
            return dict(db.execute('SELECT * FROM actions WHERE id=?',(aid,)).fetchone())

    def decide(self, actor, aid, decision):
        with self.connection(True) as db:
            row = db.execute('SELECT * FROM actions WHERE id=? AND tenant=?',(aid,actor['tenant'])).fetchone()
            if not row:
                raise LookupError('操作不存在')
            own = row['user_id'] == actor['id']
            if row['kind']=='request_access':
                allowed = (decision=='reject' and own) or (actor['role']=='admin' and not own and decision in ('approve','reject'))
            else:
                allowed = own and decision in ('confirm','reject')
            if not allowed:
                raise PermissionError('此操作需要请求人确认或另一位管理员审批')
            if row['status']!='pending' or row['expires'] < time.time():
                raise ValueError('操作已处理或已过期，请重新发起')
            status = 'rejected' if decision=='reject' else 'completed'
            payload = json.loads(row['payload'])
            result = {'simulated':True,'message':'已取消'}
            if status=='completed':
                if row['kind']=='request_access':
                    db.execute('INSERT INTO grants VALUES(?,?,?,?) ON CONFLICT(tenant,user_id,resource) DO NOTHING',(actor['tenant'],row['user_id'],payload['resource'],time.time()))
                result={'simulated':True,'message':'模拟操作完成；未修改真实账号或权限','reference':aid[:12]}
            db.execute('UPDATE actions SET status=?,result=?,reviewer=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),actor['id'],aid))
            self.audit(db,actor,'action.'+status,aid,{'kind':row['kind'],'requester':row['user_id']})
            return {**dict(row),'status':status,'result':result}
