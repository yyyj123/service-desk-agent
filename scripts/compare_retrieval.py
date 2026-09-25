"""Reproduce the reranker ablation on the existing demo evaluation set."""
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from service.config import Settings
from service.retrieval import KnowledgeBase
from scripts.evaluate_retrieval import CASES

def main():
    settings=Settings();kb=KnowledgeBase(settings);report={}
    for enabled in (False,True):
        settings.rerank_enabled=enabled;rows=[];started=time.perf_counter()
        for question,expected in CASES:
            hits=kb.search(question,{'tenant':'demo','role':'employee'},top_k=3)
            rank=next((i+1 for i,r in enumerate(hits) if r['source'].startswith(expected+' /')),0)
            rows.append({'question':question,'rank':rank,'sources':[{'source':h['source'],'score':h.get('rerank_score')} for h in hits]})
        negatives=[{'question':q,'hits':len(kb.search(q,{'tenant':'demo','role':'employee'}))} for q in ['番茄意面怎么做','月球面积多大','Python 如何读取 JSON 文件']]
        result={'hit_at_3':sum(r['rank']>0 for r in rows)/len(rows),'mrr_at_3':sum(1/r['rank'] if r['rank'] else 0 for r in rows)/len(rows),'seconds':round(time.perf_counter()-started,2),'negatives':negatives,'rows':rows}
        report['rerank_on' if enabled else 'rerank_off']=result
    report['scope']='Same 20 demo positives and 3 negatives; not held-out and not answer accuracy. No comparison against another deployed system.'
    report['threshold']=settings.rerank_threshold
    (ROOT/'reports/rerank-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:{a:b for a,b in v.items() if a!='rows'} if isinstance(v,dict) else v for k,v in report.items()},ensure_ascii=True))

if __name__=='__main__':main()
