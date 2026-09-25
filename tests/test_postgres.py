"""Run only against the dedicated local validation database.

TEST_POSTGRES_URL=postgresql://.../desk_validation pytest tests/test_postgres.py
These integration tests clear the application's tables in that test database.
"""
import asyncio
import base64
import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import pytest
from fastapi.testclient import TestClient
from service.api import create_app
from service.config import Settings
from service.retrieval import KnowledgeBase
from service.security import hasher
from service.store import Store

URL=os.getenv('TEST_POSTGRES_URL','')
pytestmark=pytest.mark.skipif(not URL,reason='Dedicated PostgreSQL integration database not configured')

class Agent:
    async def run(self,actor,question,history,emit=None):
        await asyncio.sleep(.2)
        if emit: await emit('delta','验证回答')
        return {'answer':'验证回答','mode':'knowledge','citations':[], 'trace':[], 'actions':[]}

@pytest.fixture
def pg(tmp_path):
    assert urlparse(URL).hostname in ('127.0.0.1','localhost')
    assert urlparse(URL).path=='/desk_validation'
    settings=Settings(database_url=URL,data_dir=tmp_path,jwt_secret='p'*48,metrics_token='m'*48)
    store=Store(URL,settings.jwt_secret)
    kb=KnowledgeBase(settings,embedder=lambda texts:[[1.,0.,0.] for _ in texts])
    with store.connection(True) as db:
        db.execute('TRUNCATE users,sessions,rate_events,conversations,messages,requests,actions,audit,grants,answer_details,feedback,chat_stats,knowledge_docs,desk_vectors RESTART IDENTITY')
        for uid,role,tenant in [('employee','employee','demo'),('admin','admin','demo'),('other','admin','elsewhere')]:
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uid,tenant,uid,hasher.hash('test-password-long'),role))
    app=create_app(settings,kb,Agent())
    with TestClient(app) as client:
        def auth(name):
            r=client.post('/api/auth/login',json={'username':name,'password':'test-password-long'})
            assert r.status_code==200,r.text
            return {'Authorization':'Bearer '+r.json()['access_token']}
        yield client,app,settings,kb,auth

def test_recreated_app_preserves_chat_sessions_feedback_and_audit(pg):
    client,app,settings,kb,auth=pg
    headers=auth('employee')
    body={'message':'VPN','request_id':'pg-persist-001'}
    r=client.post('/api/chat',headers=headers,json=body);assert r.status_code==200,r.text
    result=r.json()
    assert client.post('/api/feedback',headers=headers,json={'message_id':result['message_id'],'rating':1}).status_code==200
    second=create_app(settings,KnowledgeBase(settings),Agent())
    with TestClient(second) as other:
        assert other.get('/api/auth/me',headers=headers).status_code==200
        assert len(other.get('/api/conversations/'+result['conversation_id'],headers=headers).json())==2
        assert other.post('/api/chat',headers=headers,json=body).json()==result
    assert app.state.store.verify_audit()

def test_pg_vectors_enforce_visibility_tenant_and_survive_recreation(pg):
    _,_,settings,kb,_=pg
    kb.collection.upsert(ids=['public','private','foreign'],documents=['VPN public','VPN private','VPN foreign'],
        metadatas=[{'tenant':t,'visibility':v,'title':v,'source':'test'} for t,v in [('demo','employee'),('demo','admin'),('elsewhere','employee')]],embeddings=[[1,0,0]]*3)
    other=KnowledgeBase(settings,embedder=lambda _: [[1,0,0]])
    assert [r['id'] for r in other.search('VPN',{'tenant':'demo','role':'employee'})]==['public']
    assert {r['id'] for r in other.search('VPN',{'tenant':'demo','role':'admin'})}=={'public','private'}

def test_document_and_vector_atomic_rollback_and_update(pg,monkeypatch):
    client,app,_,kb,auth=pg;headers=auth('admin')
    body={'title':'VPN指南','filename':'guide.txt','content_base64':base64.b64encode('VPN连接步骤'.encode()).decode(),'visibility':'employee'}
    r=client.post('/api/admin/knowledge',headers=headers,json=body);assert r.status_code==200,r.text
    doc=r.json();old=kb.collection.get(include=['documents','metadatas','embeddings'])
    original=app.state.store.audit
    def fail(*args,**kwargs):raise RuntimeError('injected commit-path failure')
    monkeypatch.setattr(app.state.store,'audit',fail)
    replacement={**body,'revision':1,'content_base64':base64.b64encode('VPN新版本'.encode()).decode()}
    assert client.put('/api/admin/knowledge/'+doc['id'],headers=headers,json=replacement).status_code==500
    assert kb.collection.get(include=['documents','metadatas','embeddings'])==old
    assert client.get('/api/admin/knowledge',headers=headers).json()[0]['revision']==1
    monkeypatch.setattr(app.state.store,'audit',original)
    assert client.put('/api/admin/knowledge/'+doc['id'],headers=headers,json=replacement).json()['revision']==2
    assert client.delete('/api/admin/knowledge/'+doc['id']+'?revision=2',headers=headers).status_code==200
    assert kb.collection.count()==0
    assert app.state.store.verify_audit()

def test_parallel_approval_and_shared_limiter(pg):
    _,app,settings,_,_=pg;s=app.state.store
    employee={'id':'employee','tenant':'demo','role':'employee'};admin={'id':'admin','tenant':'demo','role':'admin'}
    action=s.propose(employee,'request_access',{'resource':'gitlab'})
    stores=[Store(URL,settings.jwt_secret) for _ in range(2)]
    def approve(i):
        try: stores[i%2].decide(admin,action['id'],'approve');return True
        except ValueError:return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(approve,range(8)))==1
        allowed=list(pool.map(lambda i:stores[i%2].limit('shared-test',3),range(12)))
    assert allowed.count(0)==3
    assert s.verify_audit()

def test_shared_admission_cap_has_no_phantom_conversation(pg):
    client,app,settings,_,auth=pg;headers=auth('employee');settings.max_active_chats=1
    with app.state.store.connection(True) as db:
        db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',('employee','active-request','hash','running',None,time.time()))
    r=client.post('/api/chat',headers=headers,json={'message':'VPN','request_id':'new-request'})
    assert r.status_code==429
    assert r.headers['retry-after']=='10'
    assert client.get('/api/conversations',headers=headers).json()==[]
    with app.state.store.connection(True) as db: db.execute('UPDATE requests SET created=?',(time.time()-121,))
    assert client.post('/api/chat',headers=headers,json={'message':'VPN','request_id':'new-request'}).status_code==200


def test_two_instances_share_chat_capacity_and_release_it(pg):
    client,app,settings,kb,auth=pg
    settings.max_active_chats=1
    headers=auth('employee')
    entered=threading.Event()
    release=threading.Event()
    class BlockingAgent(Agent):
        async def run(self,*args,**kwargs):
            entered.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return await super().run(*args,**kwargs)
    second=create_app(settings,kb,BlockingAgent())
    with TestClient(second) as other, ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(other.post,'/api/chat',headers=headers,json={'message':'VPN','request_id':'cross-instance-001'})
        try:
            assert entered.wait(10)
            blocked=client.post('/api/chat',headers=headers,json={'message':'VPN','request_id':'cross-instance-002'})
            assert blocked.status_code==429
        finally:
            release.set()
        assert future.result(timeout=10).status_code==200
    assert client.post('/api/chat',headers=headers,json={'message':'VPN','request_id':'cross-instance-002'}).status_code==200
