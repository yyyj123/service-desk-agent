"""Permission-filtered dense/lexical fusion. Context is evidence, never instructions."""
import hashlib
import json
import math
import re
import threading
import httpx
from collections import Counter
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import OpenAI
from .telemetry import RETRIEVAL, TOKENS, RERANK

def terms(text):
    text=text.lower()
    tokens=re.findall(r'[a-z0-9]+',text)
    for run in re.findall(r'[\u4e00-\u9fff]+',text):
        tokens.extend(run[i:i+2] for i in range(max(1,len(run)-1)))
    return tokens

def bm25(query,documents):
    bags=[Counter(terms(d)) for d in documents]
    avg=sum(sum(b.values()) for b in bags)/max(1,len(bags))
    frequencies=Counter(term for bag in bags for term in bag)
    query_terms=set(terms(query))
    scores=[]
    for bag in bags:
        score=0.0
        length=sum(bag.values())
        for term in query_terms:
            count=bag[term]
            df=frequencies[term]
            idf=math.log(1+(len(bags)-df+.5)/(df+.5))
            score+=idf*count*2.5/(count+1.5*(.25+.75*length/max(1,avg)))
        scores.append(score)
    return scores

class KnowledgeBase:
    def __init__(self,settings,embedder=None):
        self.settings=settings
        fingerprint=hashlib.sha256((settings.embed_url+settings.embed_model).encode()).hexdigest()[:12]
        if settings.database_url:
            from .pg_vectors import PgCollection
            self.collection=PgCollection(settings.database_url,'it-kb-'+fingerprint)
        else:
            import chromadb
            self.client=chromadb.PersistentClient(path=str(settings.data_dir/'chroma'))
            self.collection=self.client.get_or_create_collection('it-kb-'+fingerprint,metadata={'hnsw:space':'cosine'},embedding_function=None)
        self.embedder=embedder
        self.lock=threading.RLock()

    def embed(self,texts):
        if self.embedder:
            return self.embedder(texts)
        with OpenAI(api_key=self.settings.embed_key,base_url=self.settings.embed_url,timeout=20,max_retries=1) as client:
            response=client.embeddings.create(model=self.settings.embed_model,input=texts)
            rows=response.data
            if response.usage:
                TOKENS.labels('embedding').inc(response.usage.total_tokens)
        return [r.embedding for r in sorted(rows,key=lambda x:x.index)]

    def ingest(self,directory):
        splitter=RecursiveCharacterTextSplitter(chunk_size=300,chunk_overlap=50,separators=['\n\n','\n','。','；','！','？','，',' ',''])
        records=[]
        for path in sorted(Path(directory).glob('*.json')):
            doc=json.loads(path.read_text(encoding='utf-8'))
            for i,text in enumerate(splitter.split_text(doc['text'])):
                meta={k:doc[k] for k in ('tenant','visibility','title','source')}
                meta['chunk']=i
                digest=hashlib.sha256((json.dumps(meta,sort_keys=True)+text).encode()).hexdigest()
                records.append((digest,text,meta))
        if not records:
            raise ValueError('No documents found')
        for start in range(0,len(records),16):
            batch=records[start:start+16]
            vectors=self.embed([r[1] for r in batch])
            self.collection.upsert(ids=[r[0] for r in batch],documents=[r[1] for r in batch],metadatas=[r[2] for r in batch],embeddings=vectors)
        # Remove stale chunks only after all new embeddings were successfully persisted.
        keep={r[0] for r in records}
        existing=self.collection.get(include=['metadatas'])
        stale=[i for i,m in zip(existing['ids'],existing['metadatas']) if i not in keep and not m.get('doc_id')]
        if stale:
            self.collection.delete(ids=stale)
        return len(records)

    def search(self,query,actor,top_k=4):
        return self._search(query,actor,top_k)

    def _search(self,query,actor,top_k=4):
        where={'tenant':actor['tenant']} if actor['role']=='admin' else {'$and':[{'tenant':actor['tenant']},{'visibility':'employee'}]}
        with RETRIEVAL.time():
            vector=self.embed([query])[0]
            with self.lock:
                docs=self.collection.get(where=where,include=['documents','metadatas'])
                if not docs['ids']:
                    return []
                dense=self.collection.query(query_embeddings=[vector],where=where,n_results=min(12,len(docs['ids'])),include=['distances'])
            lexical=bm25(query,docs['documents'])
            dense_ids=dense['ids'][0]
            similarities={i:1-d for i,d in zip(dense_ids,dense['distances'][0])}
            ranks={i:n for n,i in enumerate(dense_ids)}
            lex_order=sorted(range(len(lexical)),key=lambda i:lexical[i],reverse=True)
            lex_ranks={docs['ids'][i]:n for n,i in enumerate(lex_order) if lexical[i]>0}
            results=[]
            for i,ident in enumerate(docs['ids']):
                similarity=similarities.get(ident,0)
                eligible=similarity>=.50 or lexical[i]>=1.2
                if not eligible and not (self.settings.rerank_enabled and (similarity>=.25 or lexical[i]>.2)):
                    continue
                fused=(1/(60+ranks[ident]+1) if ident in ranks else 0)+(1/(60+lex_ranks[ident]+1) if ident in lex_ranks else 0)
                results.append({'id':ident,'text':docs['documents'][i],**docs['metadatas'][i],'score':round(fused,5),'similarity':round(similarity,4),'baseline_eligible':eligible})
            candidates=sorted(results,key=lambda x:x['score'],reverse=True)[:18]
            if self.settings.rerank_enabled and candidates:
                try:
                    response=httpx.post(self.settings.embed_url.rstrip('/')+'/rerank',headers={'Authorization':'Bearer '+self.settings.embed_key},json={'model':self.settings.rerank_model,'query':query,'documents':[r['text'] for r in candidates],'top_n':len(candidates),'return_documents':False},timeout=10)
                    response.raise_for_status()
                    scores=response.json()['results']
                    indices=[int(r['index']) for r in scores]
                    if len(indices)!=len(candidates) or set(indices)!=set(range(len(candidates))):
                        raise ValueError('Invalid reranking indices')
                    ranked=[]
                    for row in scores:
                        score=float(row['relevance_score'])
                        if not math.isfinite(score) or not 0<=score<=1:
                            raise ValueError('Invalid relevance score')
                        if score>=self.settings.rerank_threshold:
                            ranked.append({**candidates[int(row['index'])],'rerank_score':score})
                    candidates=sorted(ranked,key=lambda r:r['rerank_score'],reverse=True)
                    RERANK.labels('ok').inc()
                except (httpx.HTTPError,ValueError,KeyError,TypeError):
                    # Keep the existing permission and relevance filters during fallback.
                    RERANK.labels('fallback').inc()
                    candidates=[r for r in candidates if r['baseline_eligible']]
            return candidates[:min(6,max(1,top_k))]
