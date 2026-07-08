from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class ServerStatusSchema(BaseModel):
    id: int
    hostname: str
    ip_address: Optional[str] = None
    group_name: str
    status: str
    uptime_seconds: int = 0
    cpu_percent: float = 0.0
    ram_percent: float = 0.0
    gpu_data: List[Dict[str, Any]] = []
    timestamp: Optional[datetime] = None

    class Config:
        orm_mode = True
