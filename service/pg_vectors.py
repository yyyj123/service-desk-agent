"""Small-knowledge-base pgvector adapter; metadata and vectors commit together."""
import json
import math
from .postgres import transaction

def vector_literal(values):
    values = [float(v) for v in values]
    if not values or not all(math.isfinite(v) for v in values):
        raise ValueError('Embedding must contain finite numbers')
    return '[' + ','.join(str(v) for v in values) + ']'

def predicate(where):
    if not where: return 'TRUE', []
    if set(where) == {'$and'}:
        parts = [predicate(x) for x in where['$and']]
        return '('+' AND '.join(p[0] for p in parts)+')', [v for p in parts for v in p[1]]
    parts, args = [], []
    for key, value in where.items():
        if key not in {'tenant','visibility','doc_id','source','title'} or not isinstance(value,str):
            raise ValueError('Unsupported metadata filter')
        parts.append('metadata->>? = ?')
        args.extend([key,value])
    return '('+' AND '.join(parts)+')',args

class PgCollection:
    def __init__(self, url, name):
        self.url, self.name = url, name
        with transaction(url,True) as db:
            db.execute('CREATE EXTENSION IF NOT EXISTS vector')
            db.execute('''CREATE TABLE IF NOT EXISTS desk_vectors(
                collection TEXT NOT NULL,id TEXT NOT NULL,document TEXT NOT NULL,
                metadata JSONB NOT NULL,embedding vector NOT NULL,
                PRIMARY KEY(collection,id))''')
            db.execute("CREATE INDEX IF NOT EXISTS desk_vectors_tenant ON desk_vectors(collection,(metadata->>'tenant'))")

    def count(self):
        with transaction(self.url) as db:
            return db.execute('SELECT COUNT(*) FROM desk_vectors WHERE collection=?',(self.name,)).fetchone()[0]

    def get(self, where=None, include=None, ids=None):
        condition,args=predicate(where)
        if ids is not None:
            condition+=' AND id = ANY(?)';args.append(list(ids))
        wanted=set(include or ['documents','metadatas'])
        columns=['id']
        if 'documents' in wanted: columns.append('document')
        if 'metadatas' in wanted: columns.append('metadata')
        if 'embeddings' in wanted: columns.append('embedding::text AS embedding')
        with transaction(self.url) as db:
            rows=db.execute('SELECT '+','.join(columns)+' FROM desk_vectors WHERE collection=? AND '+condition+' ORDER BY id',[self.name,*args]).fetchall()
        result={'ids':[r['id'] for r in rows]}
        if 'documents' in wanted: result['documents']=[r['document'] for r in rows]
        if 'metadatas' in wanted: result['metadatas']=[r['metadata'] for r in rows]
        if 'embeddings' in wanted: result['embeddings']=[json.loads(r['embedding']) for r in rows]
        return result

    def upsert(self, ids, documents, metadatas, embeddings):
        if len({len(ids),len(documents),len(metadatas),len(embeddings)}) != 1:
            raise ValueError('Mismatched vector record lengths')
        with transaction(self.url,True) as db:
            for ident,doc,meta,vector in zip(ids,documents,metadatas,embeddings):
                if not meta.get('tenant') or meta.get('visibility') not in ('employee','admin'):
                    raise ValueError('Missing vector access policy')
                db.execute('''INSERT INTO desk_vectors VALUES(?,?,?,?::jsonb,?::vector)
                    ON CONFLICT(collection,id) DO UPDATE SET document=excluded.document,
                    metadata=excluded.metadata,embedding=excluded.embedding''',
                    (self.name,ident,doc,json.dumps(meta,ensure_ascii=False),vector_literal(vector)))

    def delete(self, ids=None, where=None):
        if ids is None and not where: raise ValueError('An explicit deletion filter is required')
        condition,args=predicate(where)
        if ids is not None:
            condition+=' AND id = ANY(?)';args.append(list(ids))
        with transaction(self.url,True) as db:
            db.execute('DELETE FROM desk_vectors WHERE collection=? AND '+condition,[self.name,*args])

    def query(self, query_embeddings, where=None, n_results=4, include=None):
        condition,args=predicate(where)
        result={'ids':[],'distances':[]}
        with transaction(self.url) as db:
            for vector in query_embeddings:
                rows=db.execute('''SELECT id,embedding <=> ?::vector AS distance FROM desk_vectors
                    WHERE collection=? AND '''+condition+' ORDER BY distance,id LIMIT ?',
                    [vector_literal(vector),self.name,*args,n_results]).fetchall()
                result['ids'].append([r['id'] for r in rows])
                result['distances'].append([r['distance'] for r in rows])
        return result
