import json
import logging
from logging.handlers import RotatingFileHandler
from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry()
REQUESTS = Counter('it_http_requests_total','HTTP requests',['method','route','status'],registry=REGISTRY)
LATENCY = Histogram('it_http_request_duration_seconds','HTTP latency',['route'],buckets=(.01,.05,.1,.25,.5,1,2,5,10,30,60,120),registry=REGISTRY)
TOKENS = Counter('it_llm_tokens_total','Provider-reported model tokens',['kind'],registry=REGISTRY)
TOOLS = Counter('it_tool_calls_total','Tool executions',['tool','outcome'],registry=REGISTRY)
BLOCKED = Counter('it_security_blocks_total','Rejected security requests',['reason'],registry=REGISTRY)
RETRIEVAL = Histogram('it_retrieval_duration_seconds','Retrieval latency',registry=REGISTRY)
RERANK = Counter('it_rerank_requests_total','Reranker success and fallback',['outcome'],registry=REGISTRY)
CHAT_TOTAL = Histogram('it_chat_completion_seconds','Full chat completion latency',registry=REGISTRY)
FIRST_TOKEN = Histogram('it_chat_first_token_seconds','Time to first answer token',registry=REGISTRY)

class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({'time':self.formatTime(record),'level':record.levelname,'event':record.getMessage(),**getattr(record,'fields',{})},ensure_ascii=False)

def setup_logging(path):
    logger = logging.getLogger('itdesk.'+str(path.resolve()))
    logger.setLevel(logging.INFO)
    logger.propagate=False
    if not logger.handlers:
        handler=RotatingFileHandler(path,maxBytes=5_000_000,backupCount=5,encoding='utf-8')
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    return logger
