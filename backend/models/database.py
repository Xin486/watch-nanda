import os
import urllib.parse  # 👇 1. 引入 Python 自带的 URL 编码库
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# 加载 .env 配置文件
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "monitor_db")

# 👇 2. 关键修复：对密码进行 URL 安全转义（这样即使密码有 @、#、% 都能完美解析）
SAFE_PASSWORD = urllib.parse.quote_plus(DB_PASSWORD)

# 👇 3. 使用转义后的 SAFE_PASSWORD 来拼接连接字符串
SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{SAFE_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
