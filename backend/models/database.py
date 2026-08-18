"""数据库引擎与 Session 管理"""
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from core.config import settings

# 连接池：常规 20 个连接、最多溢出 10 个；pre_ping 防止 MySQL 断开后复用死连接
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 所有 ORM 模型的基类（模型定义见 models/models.py）
Base = declarative_base()


def get_db():
    """FastAPI 依赖：为每个请求提供一个数据库 Session，请求结束后自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
