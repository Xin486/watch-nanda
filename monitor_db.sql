create table alert_configs
(
    id                        int auto_increment
        primary key,
    enable_email_alert        tinyint(1) default 0 null,
    offline_threshold_minutes int        default 5 null,
    alert_recipient           varchar(255)         null
);

create table email_configs
(
    id            int auto_increment
        primary key,
    smtp_server   varchar(255)                        null,
    smtp_port     int          default 465            null,
    smtp_user     varchar(255)                        null,
    smtp_password varchar(255)                        null,
    from_name     varchar(255) default 'Node Monitor' null,
    from_address  varchar(255)                        null,
    use_tls       tinyint(1)   default 1              null
);

create table server_stats
(
    id             int auto_increment
        primary key,
    server_id      int                                   not null,
    timestamp      datetime    default CURRENT_TIMESTAMP null,
    status         varchar(20) default 'offline'         null,
    uptime_seconds int         default 0                 null,
    cpu_percent    float       default 0                 null,
    cpu_cores      int         default 1                 null,
    ram_total_mb   int         default 0                 null,
    ram_used_mb    int         default 0                 null,
    ram_percent    float       default 0                 null,
    gpu_data       json                                  null
);

create index ix_server_stats_server_id
    on server_stats (server_id);

create index ix_server_stats_timestamp
    on server_stats (timestamp);

-- 复合索引：加速按 server_id + 时间范围的历史查询
create index ix_server_stats_sid_ts
    on server_stats (server_id, timestamp);

create table servers
(
    id                 int auto_increment
        primary key,
    hostname           varchar(100)                           not null,
    ip_address         varchar(50)                            null,
    ssh_port           int          default 22                null,
    ssh_user           varchar(50)  default 'root'            null,
    group_name         varchar(50)  default '未分组'          null,
    is_active          tinyint(1)   default 1                 null,
    status             varchar(20)  default 'offline'         null,
    last_online        datetime                               null,
    offline_since      datetime                               null,
    latest_status_data json                                   null,
    locked_until       datetime                               null,
    created_at         datetime     default CURRENT_TIMESTAMP null,
    description        varchar(255) default ''                null,
    updated_at         datetime     default CURRENT_TIMESTAMP null on update CURRENT_TIMESTAMP,
    last_alert_time    datetime                               null
);

create index ix_servers_hostname
    on servers (hostname);

