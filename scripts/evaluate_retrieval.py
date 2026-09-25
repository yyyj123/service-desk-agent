"""Small human-authored demo retrieval evaluation, not answer accuracy."""
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from service.config import Settings
from service.retrieval import KnowledgeBase

CASES=[('VPN 报错 809 如何解决','IT-KB-001'),('远程连接 VPN 需要多因素认证吗','IT-KB-001'),('VPN 连上以后内部网页打不开','IT-KB-001'),('我没有远程访问权限怎么办','IT-KB-001'),('VPN 故障工单要收集哪些信息','IT-KB-001'),('忘记公司账号密码了','IT-KB-002'),('可以为同事重置密码吗','IT-KB-002'),('密码长度有什么要求','IT-KB-002'),('账号连续输错密码被锁定多久','IT-KB-002'),('手机换了导致 MFA 无法使用怎么办','IT-KB-002'),('申请 GitLab 权限需要哪些内容','IT-KB-003'),('能不能自己批准权限申请','IT-KB-003'),('支持申请哪些资源的权限','IT-KB-003'),('能直接获取 root 权限吗','IT-KB-003'),('离职回收账号权限怎么处理','IT-KB-003'),('如何提交 IT 工单','IT-KB-004'),('报障应该写哪些信息','IT-KB-004'),('工单会发送给真实 IT 团队吗','IT-KB-004'),('服务状态数据是真实的吗','IT-KB-004'),('安全事件应该找谁处理','IT-KB-004')]

def main():
    kb=KnowledgeBase(Settings())
    rows=[]
    for question,expected in CASES:
        start=time.perf_counter()
        result=kb.search(question,{'tenant':'demo','role':'employee'},top_k=3)
        rank=next((i+1 for i,r in enumerate(result) if r['source'].startswith(expected+' /')),0)
        rows.append({'question':question,'expected_source':expected,'rank':rank,'hit':rank>0,'latency_ms':round((time.perf_counter()-start)*1000,2)})
    negatives=[]
    for question in ['番茄意面怎么做','月球面积多大','Python 如何读取 JSON 文件']:
        result=kb.search(question,{'tenant':'demo','role':'employee'})
        negatives.append({'question':question,'no_matches':not result,'returned':len(result)})
    report={'scope':'20 manually authored positive and 3 negative questions against demo documents. Retrieval only; not answer accuracy and not a held-out production dataset.','questions':len(rows),'hit_at_3':sum(r['hit'] for r in rows)/len(rows),'mrr_at_3':sum(1/r['rank'] if r['rank'] else 0 for r in rows)/len(rows),'negatives':negatives,'results':rows}
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/retrieval-evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='results'},ensure_ascii=False))
if __name__=='__main__':
    main()
