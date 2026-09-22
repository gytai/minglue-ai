-- ============================================================================
-- 明略后台增量迁移脚本（PostgreSQL）
--
-- 适用：从 Stage 1 及更早的库结构升级到 Stage 2（GYTAI-128）结构。
-- 特性：可重复执行（幂等）。重复执行不会报错，不会产生重复菜单/权限记录。
--
-- 执行方式：
--   psql -U <user> -d <db> -f sql/migration/20260922_minglue_stage2_pg.sql
--
-- 设计说明：
--   1) 迁移只做"加列 / 加表 / 加索引 / 加菜单"，不删除任何既有列与数据。
--   2) 幂等依赖 ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
--      （PostgreSQL 9.6+）。
--   3) 菜单权限种子数据用 WHERE NOT EXISTS 判重。
--   4) 不写入任何厂商凭证、token 或 Apifox 密码。
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 1、ml_device：补齐在线三态、心跳原值时间、配置状态
-- ---------------------------------------------------------------------------
alter table ml_device add column if not exists online_status int4;
alter table ml_device add column if not exists device_status int4;
alter table ml_device add column if not exists heartbeat_time timestamp(0);
alter table ml_device add column if not exists config_last_upload_time timestamp(0);
alter table ml_device add column if not exists config_synced_at timestamp(0);
create index if not exists idx_ml_device_online_status on ml_device(online_status);
comment on column ml_device.online_status is '心跳 online 三态：2在线 1开机未在线 0离线';
comment on column ml_device.device_status is '心跳 device_status：1正常在线 0离线';
comment on column ml_device.heartbeat_time is '设备上报的心跳时间原值';
comment on column ml_device.config_last_upload_time is '厂商配置最后上传时间';
comment on column ml_device.config_synced_at is '本地配置同步时间';

-- ---------------------------------------------------------------------------
-- 2、ml_callback_log：幂等键改造 + 重复标记 + 审计时间 + session/topic
--
--    老库 uk_ml_callback_event_id 唯一约束建在可空列 event_id 上，
--    PostgreSQL 唯一约束对 NULL 不去重（心跳等无业务键回调会重复落库）。
--    本迁移新增非空列 dedup_key 并让它唯一，event_id 降级为普通索引。
-- ---------------------------------------------------------------------------
alter table ml_callback_log add column if not exists dedup_key varchar(128);
alter table ml_callback_log add column if not exists log_type varchar(100);
alter table ml_callback_log add column if not exists session_id int8;
alter table ml_callback_log add column if not exists topic_name varchar(64);
alter table ml_callback_log add column if not exists item_count int4 default 1;
alter table ml_callback_log add column if not exists duplicate_flag char(1) default 'N';
alter table ml_callback_log add column if not exists processed_at timestamp(0);

-- 老数据回填 dedup_key：优先业务键，其次主键，保证非空且唯一。
update ml_callback_log
set dedup_key = case
        when event_id is not null and event_id <> '' then 'legacy:' || event_type || ':' || event_id
        else 'legacy:pk:' || callback_id::text
    end
where dedup_key is null or dedup_key = '';

alter table ml_callback_log alter column dedup_key set not null;
alter table ml_callback_log alter column duplicate_flag set default 'N';
update ml_callback_log set duplicate_flag = 'N' where duplicate_flag is null;
alter table ml_callback_log alter column duplicate_flag set not null;

-- 干掉可空列上的唯一约束，换成非空 dedup_key 唯一
alter table ml_callback_log drop constraint if exists uk_ml_callback_event_id;
alter table ml_callback_log drop constraint if exists uk_ml_callback_dedup_key;
alter table ml_callback_log add constraint uk_ml_callback_dedup_key unique (dedup_key);

create index if not exists idx_ml_callback_event_id on ml_callback_log(event_id);
create index if not exists idx_ml_callback_device_type on ml_callback_log(device_code, event_type);
create index if not exists idx_ml_callback_status_received on ml_callback_log(process_status, received_at);

-- ---------------------------------------------------------------------------
-- 3、新增业务表：录音/转码/ASR 产物、设备控制指令日志
-- ---------------------------------------------------------------------------
create table if not exists ml_recording_file (
    recording_id bigserial not null,
    object_key varchar(500) not null,
    device_code varchar(100),
    session_id int8,
    callback_session_id int8,
    record_no varchar(255),
    audio_id varchar(100),
    duration int8,
    size int8,
    download_url varchar(1000),
    merge_success_time timestamp(0),
    is_eof char(1) default 'N',
    record_status varchar(20) not null default 'uploaded',
    transcode_task_id varchar(128),
    transcode_status varchar(50),
    group_key varchar(128),
    transcode_file_path varchar(500),
    transcode_download_url varchar(1000),
    transcode_finished_at timestamp(0),
    asr_task_id varchar(128),
    asr_status varchar(50),
    asr_text_object_key varchar(500),
    asr_text_json text,
    event_time timestamp(0),
    create_time timestamp(0),
    update_time timestamp(0),
    primary key (recording_id),
    constraint uk_ml_recording_object_key unique (object_key)
);
create index if not exists idx_ml_recording_device on ml_recording_file(device_code);
create index if not exists idx_ml_recording_task on ml_recording_file(transcode_task_id);
create index if not exists idx_ml_recording_asr on ml_recording_file(asr_task_id);
comment on table ml_recording_file is '录音文件与转码/ASR产物表';

create table if not exists ml_device_control_log (
    control_id bigserial not null,
    device_code varchar(100) not null,
    command varchar(50) not null,
    audio_id varchar(100),
    msg_id varchar(128),
    request_status varchar(20) not null default 'success',
    remote_status int4,
    payload_json text,
    response_json text,
    error_message varchar(1000),
    operator varchar(64) default '',
    request_ip varchar(128),
    create_time timestamp(0),
    update_time timestamp(0),
    primary key (control_id),
    constraint uk_ml_control_msg_id unique (msg_id)
);
create index if not exists idx_ml_control_device on ml_device_control_log(device_code);
create index if not exists idx_ml_control_created on ml_device_control_log(create_time);
comment on table ml_device_control_log is '设备控制指令日志表';

-- ---------------------------------------------------------------------------
-- 4、菜单与权限种子数据（WHERE NOT EXISTS 判重，不产生重复记录）
--
--    注意：`values (...)` 会把未标注的字面量统一推断为 text，直接 insert 到
--    timestamp 列会报 "column is of type timestamp but expression is of type text"。
--    因此在子查询里对 create_time / update_time 显式转型。
-- ---------------------------------------------------------------------------
insert into sys_menu (menu_id, menu_name, parent_id, order_num, path, component, query, route_name,
                      is_frame, is_cache, menu_type, visible, status, perms, icon, create_by,
                      create_time, update_by, update_time, remark)
select seed.menu_id, seed.menu_name, seed.parent_id, seed.order_num, seed.path, seed.component,
       seed.query, seed.route_name, seed.is_frame, seed.is_cache, seed.menu_type, seed.visible,
       seed.status, seed.perms, seed.icon, seed.create_by,
       seed.create_time::timestamp(0), seed.update_by, seed.update_time::timestamp(0), seed.remark
from (values
    (118, '设备管理', 5, 1, 'manage', 'device.manage', '', '', 1, 0, 'C', '0', '0',
     'device:manage:list', 'antd-mobile', 'admin', now()::text, '', null, '明略硬件设备管理'),
    (119, '回调日志', 5, 2, 'callback', 'device.callback_log', '', '', 1, 0, 'C', '0', '0',
     'device:callback:list', 'antd-cloud-download', 'admin', now()::text, '', null, '设备回调日志'),
    (1061, '设备查询', 118, 1, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:manage:query', '#', 'admin', now()::text, '', null, ''),
    (1062, '设备新增', 118, 2, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:manage:add', '#', 'admin', now()::text, '', null, ''),
    (1063, '设备修改', 118, 3, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:manage:edit', '#', 'admin', now()::text, '', null, ''),
    (1064, '设备删除', 118, 4, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:manage:remove', '#', 'admin', now()::text, '', null, ''),
    (1065, '回调查询', 119, 1, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:callback:query', '#', 'admin', now()::text, '', null, ''),
    (1066, '回调删除', 119, 2, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:callback:remove', '#', 'admin', now()::text, '', null, ''),
    (1067, '设备控制', 118, 5, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:manage:control', '#', 'admin', now()::text, '', null, ''),
    (1068, '录音记录查询', 118, 6, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:recording:list', '#', 'admin', now()::text, '', null, ''),
    (1069, '控制日志查询', 118, 7, '#', '', '', '', 1, 0, 'F', '0', '0',
     'device:control:list', '#', 'admin', now()::text, '', null, '')
) as seed(menu_id, menu_name, parent_id, order_num, path, component, query, route_name,
          is_frame, is_cache, menu_type, visible, status, perms, icon, create_by,
          create_time, update_by, update_time, remark)
where not exists (select 1 from sys_menu m where m.menu_id = seed.menu_id);

commit;
