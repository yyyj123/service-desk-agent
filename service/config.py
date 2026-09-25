"""Environment configuration. Secrets are never returned by the HTTP API."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
load_dotenv(ROOT / '.env.enterprise')

@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('IT_DATA_DIR', str(ROOT / 'runtime'))))
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    max_active_chats: int = field(default_factory=lambda: int(os.getenv("IT_MAX_ACTIVE_CHATS", "4")))
    jwt_secret: str = field(default_factory=lambda: os.getenv('IT_JWT_SECRET', ''))
    metrics_token: str = field(default_factory=lambda: os.getenv('IT_METRICS_TOKEN', ''))
    model: str = field(default_factory=lambda: os.getenv('IT_CHAT_MODEL', 'glm-4-flash'))
    chat_url: str = field(default_factory=lambda: os.getenv('IT_CHAT_URL', 'https://open.bigmodel.cn/api/paas/v4/'))
    chat_key: str = field(default_factory=lambda: os.getenv('IT_CHAT_KEY') or os.getenv('ZHIPUAI_API_KEY', ''))
    embed_model: str = field(default_factory=lambda: os.getenv('IT_EMBED_MODEL', 'BAAI/bge-m3'))
    embed_url: str = field(default_factory=lambda: os.getenv('IT_EMBED_URL', 'https://api.siliconflow.cn/v1'))
    embed_key: str = field(default_factory=lambda: os.getenv('IT_EMBED_KEY') or os.getenv('SILICONFLOW_API_KEY', ''))
    cookie_secure: bool = field(default_factory=lambda: os.getenv('IT_COOKIE_SECURE', 'false').lower() == 'true')
    origin: str = field(default_factory=lambda: os.getenv('IT_ORIGIN', 'http://127.0.0.1:8600'))
    rpm: int = 60
    rerank_enabled: bool = field(default_factory=lambda: os.getenv('IT_RERANK_ENABLED','false').lower()=='true')
    rerank_model: str = field(default_factory=lambda: os.getenv('IT_RERANK_MODEL','BAAI/bge-reranker-v2-m3'))
    rerank_threshold: float = field(default_factory=lambda: float(os.getenv('IT_RERANK_THRESHOLD','0.25')))
    demo_login: bool = field(default_factory=lambda: os.getenv('IT_DEMO_LOGIN', 'false').lower() == 'true')
    session_seconds: int = 1800

    def validate(self):
        if len(self.jwt_secret) < 32 or len(self.metrics_token) < 32:
            raise RuntimeError('Run python -m service.manage init to create local secrets first.')
        if self.database_url and not self.database_url.startswith(("postgres://", "postgresql://")):
            raise RuntimeError("DATABASE_URL must be a PostgreSQL URL")
        if not 1 <= self.max_active_chats <= 32:
            raise RuntimeError("IT_MAX_ACTIVE_CHATS must be between 1 and 32")
        self.data_dir.mkdir(parents=True, exist_ok=True)
