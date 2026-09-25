import argparse
import json
import secrets
import uuid
from pathlib import Path
from dotenv import load_dotenv
from .config import ROOT, Settings
from .store import Store
from .security import hasher

def init():
    target=ROOT/'.env.enterprise'
    if not target.exists():
        target.write_text('IT_JWT_SECRET='+secrets.token_urlsafe(48)+'\nIT_METRICS_TOKEN='+secrets.token_urlsafe(48)+'\nIT_ORIGIN=http://127.0.0.1:8600\nIT_COOKIE_SECURE=false\n',encoding='utf-8')
    load_dotenv(target)
    settings=Settings()
    settings.validate()
    secret_dir=ROOT/'deploy'/'secrets'
    secret_dir.mkdir(parents=True,exist_ok=True)
    (secret_dir/'metrics-token').write_text(settings.metrics_token,encoding='utf-8')
    store=Store(settings.database_url or settings.data_dir/'state.db',settings.jwt_secret)
    created=[]
    with store.connection(True) as db:
        for username,role in [('employee','employee'),('admin','admin')]:
            if not db.execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():
                password=secrets.token_urlsafe(18)
                db.execute('INSERT INTO users VALUES(?,?,?,?,?)',(uuid.uuid4().hex,'demo',username,hasher.hash(password),role))
                created.append(f'{username} ({role}): {password}')
    if created:
        with (settings.data_dir/'accounts.txt').open('a',encoding='utf-8') as f:
            f.write('\n'.join(created)+'\n')
    print('Initialized. Local credentials: '+str(settings.data_dir/'accounts.txt'))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['init','ingest','serve','audit'])
    parser.add_argument('--port',type=int,default=8600)
    parser.add_argument('--host',default='127.0.0.1')
    args=parser.parse_args()
    if args.command=='init':
        init()
        return
    settings=Settings()
    settings.validate()
    if args.command=='ingest':
        from .retrieval import KnowledgeBase
        print(json.dumps({'chunks':KnowledgeBase(settings).ingest(ROOT/'knowledge')}))
    elif args.command=='audit':
        print('Audit integrity:',Store(settings.database_url or settings.data_dir/'state.db',settings.jwt_secret).verify_audit())
    else:
        import uvicorn
        uvicorn.run('service.api:create_app',factory=True,host=args.host,port=args.port,proxy_headers=False,access_log=False)

if __name__=='__main__':
    main()
