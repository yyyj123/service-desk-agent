import base64
import binascii
import json
import time
import uuid
import threading
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from langchain_text_splitters import RecursiveCharacterTextSplitter
from .documents import parse_document

_upload_slots=threading.BoundedSemaphore(1)

class Upload(BaseModel):
    model_config=ConfigDict(extra='forbid')
    title:str=Field(min_length=1,max_length=120)
    filename:str=Field(min_length=1,max_length=180)
    content_base64:str=Field(min_length=1,max_length=1_340_000)
    visibility:Literal['employee','admin']='employee'
    revision:int|None=Field(default=None,ge=1)

class Feedback(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message_id:int=Field(gt=0)
    rating:Literal[-1,1]
    comment:str=Field(default='',max_length=500)

def register_management(app,store,kb,actor,admin):
    def owned_document(db,ident,who):
        row=db.execute('SELECT * FROM knowledge_docs WHERE id=? AND tenant=?',(ident,who['tenant'])).fetchone()
        if not row: raise HTTPException(404,'文档不存在')
        return row

    def snapshot(where):
        return kb.collection.get(where=where,include=['documents','metadatas','embeddings'])

    def restore(old):
        if old['ids']:
            kb.collection.upsert(ids=old['ids'],documents=old['documents'],metadatas=old['metadatas'],embeddings=old['embeddings'])

    def save_document(body,who,ident=None):
        if not _upload_slots.acquire(blocking=False):
            raise HTTPException(429,'已有文档正在入库，请稍后重试',headers={'Retry-After':'5'})
        try:
            return _save_document(body,who,ident)
        finally:
            _upload_slots.release()

    def _save_document(body,who,ident=None):
        # Check ownership before spending embedding tokens.
        if ident:
            with store.connection() as db:
                old=owned_document(db,ident,who)
                if body.revision!=old['revision']: raise HTTPException(409,'文档已更新，请刷新后再试')
        try:
            data=base64.b64decode(body.content_base64,validate=True)
            text=parse_document(body.filename,data)
        except (ValueError,binascii.Error) as exc:
            raise HTTPException(422,str(exc)) from None
        parts=RecursiveCharacterTextSplitter(chunk_size=300,chunk_overlap=50,separators=['\n\n','\n','。','；','，',' ','']).split_text(text)
        if len(parts)>450: raise HTTPException(422,'文档片段过多，请拆分上传')
        vectors=[]
        try:
            for i in range(0,len(parts),16): vectors.extend(kb.embed(parts[i:i+16]))
        except Exception:
            raise HTTPException(503,'嵌入服务暂不可用，原文档未修改') from None
        new=ident is None
        ident=ident or uuid.uuid4().hex
        version=uuid.uuid4().hex
        ids=[f'{ident}:{version}:{i}' for i in range(len(parts))]
        source=body.filename.replace('\\','/').split('/')[-1]
        metadata=[{'tenant':who['tenant'],'visibility':body.visibility,'title':body.title,'source':source,'chunk':i,'doc_id':ident} for i in range(len(parts))]
        where={'$and':[{'tenant':who['tenant']},{'doc_id':ident}]}
        with kb.lock:
            old=snapshot(where)
            try:
                with store.connection(True) as db:
                    if store.is_postgres: old=snapshot(where)
                    revision=1
                    if not new:
                        row=owned_document(db,ident,who)
                        if body.revision!=row['revision']: raise HTTPException(409,'文档已更新，请刷新后再试')
                        revision=row['revision']+1
                    kb.collection.upsert(ids=ids,documents=parts,metadatas=metadata,embeddings=vectors)
                    if old['ids']: kb.collection.delete(ids=old['ids'])
                    db.execute('INSERT INTO knowledge_docs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,source=excluded.source,visibility=excluded.visibility,revision=excluded.revision,chunks=excluded.chunks,updated=excluded.updated,content=excluded.content',(ident,who['tenant'],body.title,source,body.visibility,revision,len(parts),time.time(),text))
                    store.audit(db,who,'knowledge.created' if new else 'knowledge.updated',ident,{'revision':revision,'chunks':len(parts),'visibility':body.visibility})
            except BaseException:
                if not store.is_postgres:
                    kb.collection.delete(ids=ids)
                    restore(old)
                raise
        return {'id':ident,'revision':revision,'chunks':len(parts),'status':'ready'}

    @app.get('/api/admin/knowledge')
    def documents(who=Depends(admin)):
        with store.connection() as db:
            return [dict(r) for r in db.execute('SELECT id,title,source,visibility,revision,chunks,updated FROM knowledge_docs WHERE tenant=? ORDER BY updated DESC',(who['tenant'],))]

    @app.post('/api/admin/knowledge')
    def upload(body:Upload,who=Depends(admin)):
        return save_document(body,who)

    @app.put('/api/admin/knowledge/{ident}')
    def replace(ident:str,body:Upload,who=Depends(admin)):
        return save_document(body,who,ident)

    @app.delete('/api/admin/knowledge/{ident}')
    def delete(ident:str,revision:int,who=Depends(admin)):
        with kb.lock:
            where={'$and':[{'tenant':who['tenant']},{'doc_id':ident}]}
            old=snapshot(where)
            try:
                with store.connection(True) as db:
                    if store.is_postgres: old=snapshot(where)
                    row=owned_document(db,ident,who)
                    if revision!=row['revision']: raise HTTPException(409,'文档已更新，请刷新后再试')
                    if old['ids']: kb.collection.delete(ids=old['ids'])
                    db.execute('DELETE FROM knowledge_docs WHERE id=? AND tenant=?',(ident,who['tenant']))
                    store.audit(db,who,'knowledge.deleted',ident,{'revision':revision})
            except BaseException:
                if not store.is_postgres: restore(old)
                raise
        return {'ok':True}

    @app.post('/api/feedback')
    def feedback(body:Feedback,who=Depends(actor)):
        with store.connection(True) as db:
            message=db.execute("SELECT m.id FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND m.role='assistant' AND c.user_id=? AND c.tenant=?",(body.message_id,who['id'],who['tenant'])).fetchone()
            if not message: raise HTTPException(404,'回答不存在')
            db.execute('INSERT INTO feedback VALUES(?,?,?,?,?) ON CONFLICT(message_id,user_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,updated=excluded.updated',(body.message_id,who['id'],body.rating,body.comment,time.time()))
            store.audit(db,who,'answer.feedback',str(body.message_id),{'rating':body.rating})
        return {'ok':True}

    @app.get('/api/admin/analytics')
    def analytics(who=Depends(admin)):
        since=time.time()-30*86400
        with store.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM chat_stats WHERE tenant=? AND created>=?',(who['tenant'],since))]
            ratings=[r['rating'] for r in db.execute('SELECT f.rating FROM feedback f JOIN chat_stats s ON s.message_id=f.message_id WHERE s.tenant=? AND s.created>=?',(who['tenant'],since))]
            latest=[dict(r) for r in db.execute('SELECT s.question,f.rating,f.comment,f.updated FROM feedback f JOIN chat_stats s ON s.message_id=f.message_id WHERE s.tenant=? ORDER BY f.updated DESC LIMIT 20',(who['tenant'],))]
        latencies=sorted(r['latency'] for r in rows)
        from collections import Counter
        counts=Counter(r['question'] for r in rows)
        days=Counter(time.strftime('%Y-%m-%d',time.gmtime(r['created'])) for r in rows)
        return {'window_days':30,'requests':len(rows),'knowledge':sum(r['mode']=='knowledge' for r in rows),'general':sum(r['mode']=='general' for r in rows),'blocked':sum(r['mode']=='blocked' for r in rows),'feedback_count':len(ratings),'satisfaction':sum(r==1 for r in ratings)/len(ratings) if ratings else None,'p95_seconds':latencies[min(len(latencies)-1,int(len(latencies)*.95))] if latencies else None,'top_questions':[{'question':q,'count':n} for q,n in counts.most_common(10)],'unanswered':[r['question'] for r in rows if r['mode']=='general'][-20:],'daily':[{'date':d,'count':n} for d,n in sorted(days.items())],'feedback':latest}
