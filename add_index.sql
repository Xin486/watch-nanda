-- =============================================================
-- 历史查询性能优化：添加复合索引
-- 用途：加速 "按 server_id + 时间范围" 的历史负载查询
-- 执行：mysql -u root -p monitor_db < add_index.sql
-- =============================================================

-- 复合索引：覆盖最常见的历史查询模式 (WHERE server_id=? AND timestamp>=?)
CREATE INDEX IF NOT EXISTS ix_server_stats_sid_ts
    ON server_stats (server_id, timestamp);
