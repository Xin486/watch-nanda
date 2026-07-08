import os
from urllib.parse import quote_plus

class Settings:
    PROJECT_NAME: str = "Node Monitor Platform"
    
    # 1. Define your raw credentials
    DB_USER = "root"
    # Put your actual password here without worrying about special characters
    DB_PASS = "NJU.edu@2026" 
    DB_HOST = "0.0.0.0"
    DB_PORT = "3306"
    DB_NAME = "monitor_db"
    
    # 2. Safely build the URL
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASS)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    
    CELERY_BROKER_URL: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    MONITOR_TIMEOUT: int = 50
    DATA_RETENTION_RAW_DAYS: int = 7

settings = Settings()
