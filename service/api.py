import asyncio
import hashlib
import hmac
import json
import mimetypes
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from .config import Settings, ROOT
from .store import Store
from .security import login, authenticate, AuthenticationBusy
from .retrieval import KnowledgeBase
from .agent import Agent
from .telemetry import REGISTRY, REQUESTS, LATENCY, BLOCKED, setup_logging, CHAT_TOTAL, FIRST_TOKEN
from .middleware import BodyLimitMiddleware

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Login(Input):
    username: str=Field(min_length=1,max_length=80)
    password: str=Field(min_length=1,max_length=256)
class Chat(Input):
    message: str=Field(min_length=1,max_length=4000)
    conversation_id: str|None=Field(default=None,pattern='^[a-f0-9]{32}$')
    request_id: str=Field(pattern='^[a-zA-Z0-9_-]{8,80}$')
class Decision(Input):
    decision: Literal['confirm','approve','reject']

def create_app(settings=None,kb=None,agent=None):
    # Windows registry may associate .js with text/plain; nosniff rightly blocks it.
    mimetypes.add_type('text/javascript','.js')
    mimetypes.add_type('text/css','.css')
    settings=settings or Settings()
    settings.validate()
    store=Store(settings.database_url or settings.data_dir/'state.db',settings.jwt_secret)
    log=setup_logging(settings.data_dir/'events.jsonl')
    kb=kb or KnowledgeBase(settings)
    agent=agent or Agent(settings,store,kb)
    app=FastAPI(title='服务台',version='1.0.0',docs_url=None,redoc_url=None)
    app.add_middleware(BodyLimitMiddleware)
    app.state.store,app.state.agent,app.state.kb=store,agent,kb

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Do not echo rejected passwords, tokens or other submitted values.
        return JSONResponse({'detail':'请求格式无效，请检查字段长度和必填项','fields':[list(e['loc']) for e in exc.errors()]},status_code=422)

    @app.middleware('http')
    async def boundary(request,call_next):
        started=time.perf_counter()
        rid=uuid.uuid4().hex
        request.state.request_id=rid
        try:
            response=await call_next(request)
        except Exception as exc:
            log.error('request.failed',extra={'fields':{'request_id':rid,'error_type':type(exc).__name__}})
            response=JSONResponse({'detail':'服务暂时不可用，请重试','request_id':rid},status_code=500)
        route=getattr(request.scope.get('route'),'path','unmatched')
        elapsed=time.perf_counter()-started
        REQUESTS.labels(request.method,route,str(response.status_code)).inc()
        LATENCY.labels(route).observe(elapsed)
        log.info('http.request',extra={'fields':{'request_id':rid,'method':request.method,'route':route,'status':response.status_code,'duration_ms':round(elapsed*1000,2)}})
        response.headers.update({'X-Request-ID':rid,'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",'Cache-Control':'no-store'})
        return response

    async def actor(request:Request):
        header=request.headers.get('authorization','')
        bearer=header.startswith('Bearer ')
        token=header[7:] if bearer else request.cookies.get('it_session','')
        who=await asyncio.to_thread(authenticate,store,settings,token)
        if not who:
            raise HTTPException(401,'请登录，或会话已过期')
        if not bearer and request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('origin')!=settings.origin or not hmac.compare_digest(request.headers.get('x-csrf-token',''),who['csrf']):
                BLOCKED.labels('csrf').inc()
                raise HTTPException(403,'请求来源或 CSRF 校验失败')
        retry=0 if request.url.path=='/api/auth/logout' else await asyncio.to_thread(store.limit,'api:'+who['id'],settings.rpm)
        if retry:
            raise HTTPException(429,'请求过于频繁，请稍后重试',headers={'Retry-After':str(retry)})
        return who

    async def admin(who=Depends(actor)):
        if who['role']!='admin':
            raise HTTPException(403,'需要管理员权限')
        return who

    @app.post('/api/auth/login')
    async def sign_in(body:Login,request:Request,response:Response):
        if request.headers.get('origin') and request.headers['origin']!=settings.origin:
            raise HTTPException(403,'登录来源无效')
        ip=request.client.host if request.client else 'unknown'
        for bucket in ('login-ip:'+ip,'login-user:'+hashlib.sha256(body.username.encode()).hexdigest()):
            retry=await asyncio.to_thread(store.limit,bucket,10,300)
            if retry:
                raise HTTPException(429,'登录尝试过多',headers={'Retry-After':str(retry)})
        try:
            result=await asyncio.to_thread(login,store,settings,body.username,body.password)
        except AuthenticationBusy:
            raise HTTPException(429,'当前登录较多，请稍后重试',headers={'Retry-After':'2'}) from None
        if not result:
            raise HTTPException(401,'账号或密码不正确')
        response.set_cookie('it_session',result['access_token'],httponly=True,secure=settings.cookie_secure,samesite='strict',max_age=settings.session_seconds)
        return result

    @app.get('/api/demo-accounts')
    def demo_accounts(response:Response):
        response.headers['Cache-Control']='no-store'
        if not settings.demo_login:
            return {'accounts':[]}
        path=settings.data_dir/'accounts.txt'
        accounts=[]
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                name,separator,password=line.partition(': ')
                username=name.split(' ')[0]
                if separator and username in ('employee','admin'):
                    accounts.append({'username':username,'password':password})
        return {'accounts':accounts}

    @app.get('/api/auth/me')
    async def me(who=Depends(actor)):
        return {k:who[k] for k in ('id','username','role','csrf')}

    @app.post('/api/auth/logout')
    def logout(response:Response,who=Depends(actor)):
        with store.connection(True) as db:
            db.execute('UPDATE sessions SET revoked=1 WHERE jti=?',(who['jti'],))
            store.audit(db,who,'auth.logout')
        response.delete_cookie('it_session')
        return {'ok':True}

    @app.get('/api/conversations')
    def conversations(who=Depends(actor)):
        with store.connection() as db:
            return [dict(r) for r in db.execute("SELECT c.id,c.updated,COALESCE((SELECT substr(content,1,32) FROM messages WHERE conversation_id=c.id AND role='user' ORDER BY id LIMIT 1),'新会话') title FROM conversations c WHERE user_id=? AND tenant=? ORDER BY updated DESC LIMIT 50",(who['id'],who['tenant']))]

    @app.get('/api/conversations/{cid}')
    def conversation(cid:str,who=Depends(actor)):
        with store.connection() as db:
            if not db.execute('SELECT 1 FROM conversations WHERE id=? AND user_id=? AND tenant=?',(cid,who['id'],who['tenant'])).fetchone():
                raise HTTPException(404,'会话不存在')
            return [{**dict(r),'detail':json.loads(r['detail']) if r['detail'] else None} for r in db.execute('SELECT m.id,m.role,m.content,d.detail FROM messages m LEFT JOIN answer_details d ON d.message_id=m.id WHERE m.conversation_id=? ORDER BY m.id',(cid,))]

    async def execute_chat(body,who,emit=None):
        started=time.perf_counter()
        cid=body.conversation_id or uuid.uuid4().hex
        fingerprint=hashlib.sha256(json.dumps({'message':body.message,'conversation_id':body.conversation_id},sort_keys=True).encode()).hexdigest()
        lease_started=time.time()
        lease_until=lease_started+120
        def claim_request():
            with store.connection(True) as db:
                cached=db.execute('SELECT * FROM requests WHERE user_id=? AND request_id=?',(who['id'],body.request_id)).fetchone()
                if cached:
                    if cached['body_hash']!=fingerprint:
                        raise HTTPException(409,'请求编号已用于其他内容')
                    if cached['status']=='complete':
                        return json.loads(cached['response'])
                    if cached['status']=='running' and cached['created']>time.time()-120:
                        raise HTTPException(409,'请求正在处理，请稍后重试')
                    db.execute('DELETE FROM requests WHERE user_id=? AND request_id=?',(who['id'],body.request_id))
                active=db.execute("SELECT COUNT(*) FROM requests WHERE status='running' AND created>?",(time.time()-120,)).fetchone()[0]
                if active>=settings.max_active_chats:
                    raise HTTPException(429,'当前咨询较多，请稍后重试',headers={'Retry-After':'10'})
                if body.conversation_id:
                    row=db.execute('SELECT * FROM conversations WHERE id=? AND user_id=? AND tenant=?',(cid,who['id'],who['tenant'])).fetchone()
                    if not row:
                        raise HTTPException(404,'会话不存在')
                    if row['busy_until']>time.time():
                        raise HTTPException(409,'此会话正在生成回答')
                else:
                    db.execute('INSERT INTO conversations(id,user_id,tenant,updated) VALUES(?,?,?,?)',(cid,who['id'],who['tenant'],time.time()))
                db.execute('UPDATE conversations SET busy_until=? WHERE id=?',(lease_until,cid))
                db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?)',(who['id'],body.request_id,fingerprint,'running',None,lease_started))
                rows=db.execute('SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 10',(cid,)).fetchall()
            return [dict(r) for r in reversed(rows)]
        history=await asyncio.to_thread(claim_request)
        if isinstance(history,dict): return history
        try:
            call=agent.run(who,body.message,history,emit=emit) if emit else agent.run(who,body.message,history)
            result=await asyncio.wait_for(call,100)
            result['conversation_id']=cid
            def persist_answer():
                with store.connection(True) as db:
                    current=db.execute('SELECT status,created FROM requests WHERE user_id=? AND request_id=?',(who['id'],body.request_id)).fetchone()
                    owner=db.execute('SELECT busy_until FROM conversations WHERE id=?',(cid,)).fetchone()
                    if not current or current['status']!='running' or current['created']!=lease_started or not owner or owner['busy_until']!=lease_until or time.time()>lease_until:
                        raise HTTPException(409,'请求已由新的尝试接管，请刷新查看结果')
                    for role,content in (('user',body.message),('assistant',result['answer'])):
                        message_row=db.execute('INSERT INTO messages(conversation_id,role,content,created) VALUES(?,?,?,?) RETURNING id',(cid,role,content,time.time())).fetchone()
                    result['message_id']=message_row[0]
                    db.execute('INSERT INTO answer_details VALUES(?,?)',(result['message_id'],json.dumps(result,ensure_ascii=False)))
                    db.execute('INSERT INTO chat_stats VALUES(?,?,?,?,?,?,?)',(result['message_id'],who['tenant'],who['id'],body.message,result['mode'],time.perf_counter()-started,time.time()))
                    db.execute("UPDATE requests SET status='complete',response=? WHERE user_id=? AND request_id=?",(json.dumps(result,ensure_ascii=False),who['id'],body.request_id))
                    db.execute('UPDATE conversations SET updated=?,busy_until=0 WHERE id=? AND busy_until=?',(time.time(),cid,lease_until))
                    store.audit(db,who,'chat.completed',cid,{'mode':result['mode'],'tools':len(result['trace'])})
            await asyncio.to_thread(persist_answer)
            CHAT_TOTAL.observe(time.perf_counter()-started)
            return result
        except (Exception,asyncio.CancelledError) as exc:
            def fail_request():
                with store.connection(True) as db:
                    db.execute("UPDATE requests SET status='failed' WHERE user_id=? AND request_id=? AND created=? AND status='running'",(who['id'],body.request_id,lease_started))
                    db.execute('UPDATE conversations SET busy_until=0 WHERE id=? AND busy_until=?',(cid,lease_until))
            await asyncio.to_thread(fail_request)
            log.warning('chat.failed',extra={'fields':{'error_type':type(exc).__name__}})
            if isinstance(exc,HTTPException): raise
            raise HTTPException(503,'模型或检索服务暂时不可用；操作申请请先在待办中核对后再重试') from exc

    @app.post('/api/chat')
    async def chat(body:Chat,who=Depends(actor)):
        return await execute_chat(body,who)

    app.state.chat_tasks=set()

    @app.get('/api/chat/requests/{request_id}')
    def chat_request(request_id:str,who=Depends(actor)):
        with store.connection() as db:
            row=db.execute('SELECT status,response,created FROM requests WHERE user_id=? AND request_id=?',(who['id'],request_id)).fetchone()
        if not row: raise HTTPException(404,'请求不存在')
        status='failed' if row['status']=='running' and row['created']<time.time()-120 else row['status']
        return {'status':status,'result':json.loads(row['response']) if row['response'] else None}

    @app.post('/api/chat/stream')
    async def chat_stream(body:Chat,who=Depends(actor)):
        queue=asyncio.Queue()
        disconnected=False
        first=True
        started=time.perf_counter()
        async def emit(kind,data):
            nonlocal first
            if kind=='delta' and first:
                FIRST_TOKEN.observe(time.perf_counter()-started)
                first=False
            if not disconnected:
                queue.put_nowait({'type':kind,'data':data})
        async def produce():
            try:
                result=await execute_chat(body,who,emit)
                await emit('done',result)
            except HTTPException as exc:
                await emit('error',{'message':exc.detail,'status':exc.status_code})
            except Exception:
                await emit('error',{'message':'生成失败，请在历史会话或待办中核对结果后重试','status':503})
        async def events():
            nonlocal disconnected
            task=asyncio.create_task(produce())
            app.state.chat_tasks.add(task)
            task.add_done_callback(app.state.chat_tasks.discard)
            try:
                yield 'data: '+json.dumps({'type':'request','data':{'request_id':body.request_id}},ensure_ascii=False)+'\n\n'
                while True:
                    try:
                        event=await asyncio.wait_for(queue.get(),15)
                    except asyncio.TimeoutError:
                        yield ': heartbeat\n\n'
                        continue
                    yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
                    if event['type'] in ('done','error'): break
            finally:
                # Finish the bounded request in background so reconnection cannot repeat actions.
                disconnected=True
        return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    @app.get('/api/actions')
    def actions(who=Depends(actor)):
        with store.connection() as db:
            if who['role']=='admin':
                rows=db.execute('SELECT a.*,u.username FROM actions a JOIN users u ON u.id=a.user_id WHERE a.tenant=? ORDER BY created DESC LIMIT 100',(who['tenant'],))
            else:
                rows=db.execute('SELECT a.*,u.username FROM actions a JOIN users u ON u.id=a.user_id WHERE a.tenant=? AND a.user_id=? ORDER BY created DESC LIMIT 100',(who['tenant'],who['id']))
            return [dict(r) for r in rows]

    @app.post('/api/actions/{aid}/decision')
    def decide(aid:str,body:Decision,who=Depends(actor)):
        try:
            return store.decide(who,aid,body.decision)
        except LookupError as exc:
            raise HTTPException(404,str(exc))
        except PermissionError as exc:
            raise HTTPException(403,str(exc))
        except ValueError as exc:
            raise HTTPException(409,str(exc))

    @app.get('/api/admin/audit')
    def audit(who=Depends(admin)):
        with store.connection() as db:
            rows=[dict(r) for r in db.execute('SELECT seq,ts,actor,event,target,detail FROM audit WHERE tenant=? ORDER BY seq DESC LIMIT 100',(who['tenant'],))]
        return {'integrity':store.verify_audit(),'events':rows}

    @app.get('/api/status')
    def status(who=Depends(actor)):
        return {'mode':'simulation','model':settings.model,'embedding':settings.embed_model,'chunks':kb.collection.count(),'tools':5}

    @app.get('/api/admin/runtime')
    def runtime(who=Depends(admin)):
        from .runtime import snapshot
        return {**snapshot(),'storage':'postgresql' if store.is_postgres else 'sqlite',
                'max_active_chats':settings.max_active_chats}

    @app.get('/health/live')
    def live():
        return {'status':'ok'}

    @app.get('/health/ready')
    def ready(response:Response):
        with store.connection() as db:
            db.execute('SELECT 1')
        count=kb.collection.count()
        response.status_code=200 if count else 503
        return {'ready':bool(count),'chunks':count}

    @app.get('/metrics')
    def metrics(request:Request):
        if not hmac.compare_digest(request.headers.get('authorization',''),'Bearer '+settings.metrics_token):
            raise HTTPException(401,'指标访问需要专用令牌')
        return Response(generate_latest(REGISTRY),media_type=CONTENT_TYPE_LATEST)

    from .management import register_management
    register_management(app,store,kb,actor,admin)

    @app.get('/')
    def index():
        return FileResponse(ROOT/'portal'/'index.html')
    @app.get('/api/docs')
    def docs():
        return FileResponse(ROOT/'portal'/'api.html')
    app.mount('/static',StaticFiles(directory=ROOT/'portal',check_dir=False),name='static')
    return app
