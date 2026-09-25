import base64
import io
import json
import zipfile
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from service.api import create_app
from service.config import Settings
from service.retrieval import KnowledgeBase
from service.security import hasher
from service.documents import extract,parse_document

class StreamingAgent:
    calls=0
    async def run(self,actor,question,history,emit=None):
        self.calls+=1
        if emit:
            await emit('status','检索中')
            await emit('delta','第一段')
            await emit('delta','第二段')
        return {'answer':'第一段第二段','citations':[{'id':'evidence','title':'依据','text':'text'}],'actions':[],'trace':[],'mode':'knowledge'}

@pytest.fixture
def system(tmp_path):
    settings=Settings(data_dir=tmp_path,jwt_secret='z'*48,metrics_token='m'*48,rerank_enabled=False)
    kb=KnowledgeBase(settings,embedder=lambda texts:[[1.,0.,0.] for _ in texts])
    agent=StreamingAgent()
    app=create_app(settings,kb,agent)
    with app.state.store.connection(True) as db:
        for ident,role,tenant in [('employee','employee','demo'),('admin','admin','demo'),('other','admin','other')]:
            db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(ident,tenant,ident,hasher.hash('test-password'),role))
    with TestClient(app) as client:
        def auth(ident):
            r=client.post('/api/auth/login',json={'username':ident,'password':'test-password'})
            assert r.status_code==200
            return {'Authorization':'Bearer '+r.json()['access_token']}
        yield client,app,kb,agent,auth

def upload_body(text='VPN 支持多因素认证。',visibility='employee'):
    return {'title':'测试文档','filename':'guide.md','content_base64':base64.b64encode(text.encode()).decode(),'visibility':visibility}

def test_stream_replay_recovery_history_feedback_and_tenant_stats(system):
    client,app,kb,agent,auth=system
    headers=auth('employee');body={'message':'测试','request_id':'stream-request-001'}
    r=client.post('/api/chat/stream',headers=headers,json=body)
    events=[json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: ')]
    assert [e['data'] for e in events if e['type']=='delta']==['第一段','第二段']
    result=events[-1]['data'];assert events[-1]['type']=='done'
    cached=client.get('/api/chat/requests/'+body['request_id'],headers=headers).json()
    assert cached['status']=='complete' and cached['result']==result
    assert client.post('/api/chat',headers=headers,json=body).json()==result
    assert agent.calls==1
    history=client.get('/api/conversations/'+result['conversation_id'],headers=headers).json()
    assert history[-1]['detail']['citations']==result['citations']
    vote={'message_id':result['message_id'],'rating':1,'comment':'有帮助'}
    assert client.post('/api/feedback',headers=headers,json=vote).status_code==200
    assert client.post('/api/feedback',headers=headers,json=vote).status_code==200
    other=auth('other')
    assert client.post('/api/feedback',headers=other,json=vote).status_code==404
    assert client.get('/api/chat/requests/'+body['request_id'],headers=other).status_code==404
    assert client.get('/api/admin/analytics',headers=headers).status_code==403
    stats=client.get('/api/admin/analytics',headers=auth('admin')).json()
    assert stats['requests']==1 and stats['feedback_count']==1 and stats['satisfaction']==1
    assert client.get('/api/admin/analytics',headers=other).json()['requests']==0

def test_document_permissions_versions_and_delete(system):
    client,app,kb,agent,auth=system
    employee=auth('employee');admin=auth('admin');other=auth('other')
    assert client.post('/api/admin/knowledge',headers=employee,json=upload_body()).status_code==403
    r=client.post('/api/admin/knowledge',headers=admin,json=upload_body(visibility='admin'))
    assert r.status_code==200,r.text
    doc=r.json();url='/api/admin/knowledge/'+doc['id']
    assert not kb.search('VPN',{'tenant':'demo','role':'employee'})
    assert kb.search('VPN',{'tenant':'demo','role':'admin'})
    assert not kb.search('VPN',{'tenant':'other','role':'admin'})
    assert client.put(url,headers=other,json={**upload_body(), 'revision':1}).status_code==404
    assert client.put(url,headers=admin,json={**upload_body(),'revision':99}).status_code==409
    r=client.put(url,headers=admin,json={**upload_body('更新后的 VPN 指南。'),'revision':1})
    assert r.status_code==200 and r.json()['revision']==2
    hits=kb.search('VPN',{'tenant':'demo','role':'employee'})
    assert all('更新后的' in r['text'] for r in hits) and hits
    assert client.delete(url+'?revision=1',headers=admin).status_code==409
    assert client.delete(url+'?revision=2',headers=admin).status_code==200
    assert not kb.search('VPN',{'tenant':'demo','role':'admin'})
    assert app.state.store.verify_audit()

def test_embedding_failure_preserves_old_document(system):
    client,_,kb,_,auth=system;admin=auth('admin')
    doc=client.post('/api/admin/knowledge',headers=admin,json=upload_body()).json()
    previous=kb.collection.get()
    kb.embedder=lambda texts:(_ for _ in ()).throw(RuntimeError('offline'))
    r=client.put('/api/admin/knowledge/'+doc['id'],headers=admin,json={**upload_body('新版本'),'revision':1})
    assert r.status_code==503
    assert kb.collection.get()==previous

def test_reranker_filters_and_falls_back_without_acl_leak(system,monkeypatch):
    client,_,kb,_,auth=system;admin=auth('admin')
    client.post('/api/admin/knowledge',headers=admin,json=upload_body('VPN 员工资料'))
    client.post('/api/admin/knowledge',headers=admin,json=upload_body('VPN 管理员秘密',visibility='admin'))
    kb.settings.rerank_enabled=True
    def response(url,**kwargs):
        assert all('秘密' not in d for d in kwargs['json']['documents'])
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'results':[{'index':0,'relevance_score':.1}]})
    monkeypatch.setattr('service.retrieval.httpx.post',response)
    actor={'tenant':'demo','role':'employee'}
    assert kb.search('VPN',actor)==[]
    def offline(*args,**kwargs): raise httpx.ConnectError('offline')
    monkeypatch.setattr('service.retrieval.httpx.post',offline)
    hits=kb.search('VPN',actor)
    assert hits and all('秘密' not in row['text'] for row in hits)

def test_document_parsers_and_limits():
    assert extract('.html',b'<p>Guide</p><script>secret</script>')=='Guide'
    assert parse_document('guide.md','中文指南'.encode())=='中文指南'
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="urn:word"><w:p><w:r><w:t>VPN guide</w:t></w:r></w:p></w:document>')
    assert extract('.docx',data.getvalue())=='VPN guide'
    with pytest.raises(ValueError): parse_document('run.exe',b'test')
    with pytest.raises(ValueError): parse_document('big.txt',b'x'*1_000_001)
    with pytest.raises(ValueError): extract('.txt',b'x'*100_001)

def test_document_transaction_failure_restores_vectors(system,monkeypatch):
    client,app,kb,_,auth=system;admin=auth('admin')
    doc=client.post('/api/admin/knowledge',headers=admin,json=upload_body()).json()
    previous=kb.collection.get()
    audit=app.state.store.audit
    def fail(db,who,event,*args,**kwargs):
        if event=='knowledge.updated': raise RuntimeError('simulated disk error')
        return audit(db,who,event,*args,**kwargs)
    monkeypatch.setattr(app.state.store,'audit',fail)
    r=client.put('/api/admin/knowledge/'+doc['id'],headers=admin,json={**upload_body('替换失败测试'),'revision':1})
    assert r.status_code==500
    assert kb.collection.get()==previous
    assert client.get('/api/admin/knowledge',headers=admin).json()[0]['revision']==1

def test_stream_csrf_and_failed_request_recovery(system):
    client,app,_,_,auth=system
    headers=auth('employee')
    assert client.post('/api/chat/stream',json={'message':'test','request_id':'csrf-stream-test'}).status_code==403
    with app.state.store.connection(True) as db:
        db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',('employee','stale-stream-001','hash','running',None,0))
    assert client.get('/api/chat/requests/stale-stream-001',headers=headers).json()['status']=='failed'

def test_office_spreadsheet_and_slides_parsing():
    for extension,entries,expected in [
        ('.xlsx',{'xl/sharedStrings.xml':'<sst xmlns="urn:xl"><si><t>VPN指南</t></si></sst>','xl/worksheets/sheet1.xml':'<worksheet xmlns="urn:xl"><row><c t="s"><v>0</v></c><c><v>809</v></c></row></worksheet>'},'VPN指南 | 809'),
        ('.pptx',{'ppt/slides/slide1.xml':'<slide xmlns="urn:ppt"><t>VPN指南</t></slide>'},'VPN指南')]:
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            for name,text in entries.items(): z.writestr(name,text)
        assert extract(extension,data.getvalue())==expected

def test_pdf_text_and_scanned_document_rejection():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    content=DecodedStreamObject();content.set_data(b'BT /F1 12 Tf 20 200 Td (VPN guide) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(content)
    output=io.BytesIO();writer.write(output)
    assert 'VPN guide' in parse_document('guide.pdf',output.getvalue())
    blank=PdfWriter();blank.add_blank_page(width=300,height=300);output=io.BytesIO();blank.write(output)
    with pytest.raises(ValueError,match='OCR'): parse_document('scanned.pdf',output.getvalue())


def test_upload_admission_releases_after_parse_failure(system,monkeypatch):
    import service.management as management
    client,app,kb,agent,auth=system
    headers=auth('admin')
    assert management._upload_slots.acquire(blocking=False)
    try:
        r=client.post('/api/admin/knowledge',headers=headers,json=upload_body())
        assert r.status_code==429 and r.headers['retry-after']=='5'
    finally:management._upload_slots.release()
    r=client.post('/api/admin/knowledge',headers=headers,json={**upload_body(),'content_base64':'invalid!'})
    assert r.status_code==422
    assert client.post('/api/admin/knowledge',headers=headers,json=upload_body()).status_code==200
