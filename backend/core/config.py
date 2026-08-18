"""
全局配置
=========
数据库连接信息通过 backend/.env 文件配置（重新部署时只需修改该文件，无需改代码）。

读取优先级：系统环境变量 > backend/.env 文件 > 代码默认值
模板文件见 backend/.env.example，部署时执行：
    cp .env.example .env   然后按实际环境修改其中的值
"""
import os
from pathlib import Path
from urllib.parse import quote_plus

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # 未安装 python-dotenv 时，仅使用系统环境变量

# backend 目录（.env 固定放在这里，按文件位置定位，与启动目录无关）
BASE_DIR = Path(__file__).resolve().parent.parent
if load_dotenv:
    load_dotenv(BASE_DIR / ".env")


class Settings:
    PROJECT_NAME: str = "Node Monitor Platform"

    # ---- 数据库连接（部署时修改 backend/.env 即可）----
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")   # 数据库主机（本机填 127.0.0.1）
    DB_PORT = os.getenv("DB_PORT", "3306")        # 数据库端口
    DB_USER = os.getenv("DB_USER", "root")        # 数据库用户名
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")    # 数据库密码
    DB_NAME = os.getenv("DB_NAME", "monitor_db")  # 数据库名

    # 连接串：优先用 DATABASE_URL 整体覆盖；否则用上方分项自动拼接（密码特殊字符已转义）
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    )


settings = Settings()
