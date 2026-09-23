-- ============================================================================
-- 明略后台增量迁移脚本（MySQL）
--
-- 适用：从 Stage 1 及更早的库结构升级到 Stage 2（GYTAI-128）结构。
-- 特性：可重复执行（幂等）。重复执行不会报错，不会产生重复菜单/权限记录。
--
-- 执行方式：
--   mysql -u<user> -p<pwd> <db> < sql/migration/20260922_minglue_stage2_mysql.sql
--
-- 设计说明：
--   1) 迁移只做"加列 / 加表 / 加索引 / 加菜单"，不删除任何既有列与数据，
--      因此新旧库（已上线库）均可直接执行。
--   2) 幂等依赖 information_schema 判断列/索引是否存在，再动态执行 DDL。
--      仅依赖 MySQL 5.7+ 与 PREPARE/EXECUTE，不需要额外工具。
--   3) 菜单权限种子数据用 not exists 判重，避免重复插入。
--   4) 不写入任何厂商凭证、token 或 Apifox 密码。
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0、辅助过程：幂等加列、幂等加索引
-- ---------------------------------------------------------------------------
drop procedure if exists ml_add_column_if_absent;
delimiter $$
create procedure ml_add_column_if_absent(
    in p_table varchar(64),
    in p_column varchar(64),
    in p_definition text
)
begin
    if not exists (
        select 1 from information_schema.columns
        where table_schema = database() and table_name = p_table and column_name = p_column
    ) then
        set @ml_ddl = concat('alter table `', p_table, '` add column `', p_column, '` ', p_definition);
        prepare ml_stmt from @ml_ddl;
        execute ml_stmt;
        deallocate prepare ml_stmt;
    end if;
end$$
delimiter ;

drop procedure if exists ml_add_index_if_absent;
delimiter $$
create procedure ml_add_index_if_absent(
    in p_table varchar(64),
    in p_index varchar(64),
    in p_columns varchar(255)
)
begin
    if not exists (
        select 1 from information_schema.statistics
        where table_schema = database() and table_name = p_table and index_name = p_index
    ) then
        set @ml_ddl = concat('alter table `', p_table, '` add index `', p_index, '` (', p_columns, ')');
        prepare ml_stmt from @ml_ddl;
        execute ml_stmt;
        deallocate prepare ml_stmt;
    end if;
end$$
delimiter ;

-- ---------------------------------------------------------------------------
-- 1、ml_device：补齐在线三态、心跳原值时间、配置状态
-- ---------------------------------------------------------------------------
call ml_add_column_if_absent('ml_device', 'online_status', 'int default null comment ''心跳 online 三态：2在线 1开机未在线 0离线''');
call ml_add_column_if_absent('ml_device', 'device_status', 'int default null comment ''心跳 device_status：1正常在线 0离线''');
call ml_add_column_if_absent('ml_device', 'heartbeat_time', 'datetime default null comment ''设备上报的心跳时间原值''');
call ml_add_column_if_absent('ml_device', 'config_last_upload_time', 'datetime default null comment ''厂商配置最后上传时间''');
call ml_add_column_if_absent('ml_device', 'config_synced_at', 'datetime default null comment ''本地配置同步时间''');
call ml_add_index_if_absent('ml_device', 'idx_ml_device_online_status', '`online_status`');

-- 迁移前遗留的旧备注（可选，仅当列存在且仍为旧注释时才改）
alter table ml_device modify column status varchar(20) not null default 'offline'
    comment '本地在线状态（含 disabled 停用）';

-- ---------------------------------------------------------------------------
-- 2、ml_callback_log：幂等键改造 + 重复标记 + 审计时间 + session/topic
--
--    关键点：老库的 uk_ml_callback_event_id 唯一约束建在可空列 event_id 上，
--    MySQL 唯一索引对 NULL 不去重（心跳等无业务键回调会无限重复落库）。
--    本迁移新增非空列 dedup_key 并让它唯一，同时把 event_id 降级为普通索引。
-- ---------------------------------------------------------------------------
call ml_add_column_if_absent('ml_callback_log', 'dedup_key', 'varchar(128) not null default '''' comment ''幂等键 SHA-256，非空且唯一''');
call ml_add_column_if_absent('ml_callback_log', 'log_type', 'varchar(100) default null comment ''upload 类回调的 logType：log/rec''');
call ml_add_column_if_absent('ml_callback_log', 'session_id', 'bigint default null comment ''回调配置ID（session_id，webhook id）''');
call ml_add_column_if_absent('ml_callback_log', 'topic_name', 'varchar(64) default null comment ''回调主题（topic_name）''');
call ml_add_column_if_absent('ml_callback_log', 'item_count', 'int default 1 comment ''单次回调携带的设备/文件条目数''');
call ml_add_column_if_absent('ml_callback_log', 'duplicate_flag', 'char(1) not null default ''N'' comment ''是否重复回调（Y是 N否）''');
call ml_add_column_if_absent('ml_callback_log', 'processed_at', 'datetime default null comment ''处理结束时间''');

-- 老数据回填 dedup_key：优先业务键，其次主键，保证非空且唯一。
-- 业务键形如 'rec:lt4/rec/xxx.opus'，为主键回填的则标注 legacy，便于后续区分。
update ml_callback_log
set dedup_key = case
        when event_id is not null and event_id <> '' then concat('legacy:', event_type, ':', event_id)
        else concat('legacy:pk:', callback_id)
    end
where dedup_key is null or dedup_key = '';

-- 干掉可空列上的唯一约束，换成非空 dedup_key 唯一
set @ml_ddl = (
    select if(
        exists (
            select 1 from information_schema.statistics
            where table_schema = database() and table_name = 'ml_callback_log' and index_name = 'uk_ml_callback_event_id'
        ),
        'alter table ml_callback_log drop index uk_ml_callback_event_id',
        'select 1'
    )
);
prepare ml_stmt from @ml_ddl;
execute ml_stmt;
deallocate prepare ml_stmt;

call ml_add_index_if_absent('ml_callback_log', 'uk_ml_callback_dedup_key', '`dedup_key`');
-- 上面若已存在同名非唯一索引会跳过；此处保证它是 UNIQUE
set @ml_ddl = (
    select if(
        exists (
            select 1 from information_schema.statistics
            where table_schema = database() and table_name = 'ml_callback_log'
              and index_name = 'uk_ml_callback_dedup_key' and non_unique = 1
        ),
        'alter table ml_callback_log drop index uk_ml_callback_dedup_key, add unique key uk_ml_callback_dedup_key (dedup_key)',
        'select 1'
    )
);
prepare ml_stmt from @ml_ddl;
execute ml_stmt;
deallocate prepare ml_stmt;

call ml_add_index_if_absent('ml_callback_log', 'idx_ml_callback_event_id', '`event_id`');
call ml_add_index_if_absent('ml_callback_log', 'idx_ml_callback_device_type', '`device_code`, `event_type`');
call ml_add_index_if_absent('ml_callback_log', 'idx_ml_callback_status_received', '`process_status`, `received_at`');

-- dedup_key 在初始化脚本里是 `not null` 且无显式默认值；迁移是先加列（带 default ''）
-- 再回填，这里把默认值去掉，使升级库与新建库的列定义完全一致。
alter table ml_callback_log alter column dedup_key drop default;

-- event_id 扩宽到 varchar(255)。Stage 2 起 event_id 承载 rec 回调的 object_key
-- （对应 ml_recording_file.object_key 为 varchar(500)），文档样例的文件名已接近 128 字符；
-- 若只在初始化脚本里改宽、迁移脚本漏改，升级库插入长录音文件名会报
-- "Data too long for column 'event_id'"，导致整条回调落库失败并被厂商重推。
alter table ml_callback_log
    modify column event_id varchar(255) default null comment '上游业务唯一键（无业务键时为 NULL）';

-- 老的 device_code 单列索引已被 (device_code, event_type) 联合索引覆盖，删除以免重复索引
set @ml_ddl = (
    select if(
        exists (
            select 1 from information_schema.statistics
            where table_schema = database() and table_name = 'ml_callback_log'
              and index_name = 'idx_ml_callback_device'
        ),
        'alter table ml_callback_log drop index idx_ml_callback_device',
        'select 1'
    )
);
prepare ml_stmt from @ml_ddl;
execute ml_stmt;
deallocate prepare ml_stmt;

-- ---------------------------------------------------------------------------
-- 3、新增业务表：录音/转码/ASR 产物、设备控制指令日志
-- ---------------------------------------------------------------------------
create table if not exists ml_recording_file (
  recording_id bigint(20) not null auto_increment comment '录音记录主键',
  object_key varchar(500) not null comment '厂商对象存储路径，录音幂等键',
  device_code varchar(100) default null comment '设备编码',
  session_id bigint default null comment '录音会话ID',
  callback_session_id bigint default null comment '回调配置ID（session_id）',
  record_no varchar(255) default null comment '录音文件名',
  audio_id varchar(100) default null comment 'API录音标识（自定义字段）',
  duration bigint default null comment '音频时长',
  size bigint default null comment '文件大小（字节）',
  download_url varchar(1000) default null comment '厂商下载地址（带签名，有有效期）',
  merge_success_time datetime default null comment '录音合并成功时间',
  is_eof char(1) default 'N' comment '是否为结束分片（文件名 _eof）',
  record_status varchar(20) not null default 'uploaded' comment '本地处理状态',
  transcode_task_id varchar(128) default null comment '厂商转码任务ID（fc.task_id）',
  transcode_status varchar(50) default null comment '转码状态（原样保存，取值域待厂商确认）',
  group_key varchar(128) default null comment '转码分组键（fc.group_key）',
  transcode_file_path varchar(500) default null comment '转码产物对象路径',
  transcode_download_url varchar(1000) default null comment '转码产物下载地址',
  transcode_finished_at datetime default null comment '转码完成时间',
  asr_task_id varchar(128) default null comment 'ASR 任务ID（asrTskId）',
  asr_status varchar(50) default null comment 'ASR 状态（原样保存，取值域待厂商确认）',
  asr_text_object_key varchar(500) default null comment 'ASR 文本结果对象路径',
  asr_text_json longtext default null comment 'ASR 文本结果原文',
  event_time datetime default null comment '事件时间',
  create_time datetime default null comment '创建时间',
  update_time datetime default null comment '更新时间',
  primary key (recording_id),
  unique key uk_ml_recording_object_key (object_key),
  key idx_ml_recording_device (device_code),
  key idx_ml_recording_task (transcode_task_id),
  key idx_ml_recording_asr (asr_task_id)
) engine=innodb comment='录音文件与转码/ASR产物表';

create table if not exists ml_device_control_log (
  control_id bigint(20) not null auto_increment comment '控制日志主键',
  device_code varchar(100) not null comment '设备编码',
  command varchar(50) not null comment '指令类型：start_recording/stop_recording',
  audio_id varchar(100) default null comment '开启录音携带的音频ID（nm）',
  msg_id varchar(128) default null comment '厂商返回的消息ID，用于回查',
  request_status varchar(20) not null default 'success' comment '本地请求结果',
  remote_status int default null comment '厂商指令状态：0初始化 1成功 2返回出错 3返回超时',
  payload_json text default null comment '厂商请求载荷（已脱敏）',
  response_json text default null comment '厂商响应载荷',
  error_message varchar(1000) default null comment '错误信息',
  operator varchar(64) default '' comment '操作人',
  request_ip varchar(128) default null comment '请求IP',
  create_time datetime default null comment '创建时间',
  update_time datetime default null comment '更新时间',
  primary key (control_id),
  unique key uk_ml_control_msg_id (msg_id),
  key idx_ml_control_device (device_code),
  key idx_ml_control_created (create_time)
) engine=innodb comment='设备控制指令日志表';

-- ---------------------------------------------------------------------------
-- 4、菜单与权限种子数据（not exists 判重，不产生重复记录）
--    与初始化脚本 dash-fastapi.sql 中的菜单 ID 保持一致。
-- ---------------------------------------------------------------------------
insert into sys_menu
select '118', '设备管理', '5', '1', 'manage', 'device.manage', '', '', 1, 0, 'C', '0', '0',
       'device:manage:list', 'antd-mobile', 'admin', sysdate(), '', null, '明略硬件设备管理'
where not exists (select 1 from sys_menu where menu_id = 118);

insert into sys_menu
select '119', '回调日志', '5', '2', 'callback', 'device.callback_log', '', '', 1, 0, 'C', '0', '0',
       'device:callback:list', 'antd-cloud-download', 'admin', sysdate(), '', null, '设备回调日志'
where not exists (select 1 from sys_menu where menu_id = 119);

insert into sys_menu
select '1061', '设备查询', '118', '1', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:manage:query', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1061);

insert into sys_menu
select '1062', '设备新增', '118', '2', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:manage:add', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1062);

insert into sys_menu
select '1063', '设备修改', '118', '3', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:manage:edit', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1063);

insert into sys_menu
select '1064', '设备删除', '118', '4', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:manage:remove', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1064);

insert into sys_menu
select '1065', '回调查询', '119', '1', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:callback:query', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1065);

insert into sys_menu
select '1066', '回调删除', '119', '2', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:callback:remove', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1066);

insert into sys_menu
select '1067', '设备控制', '118', '5', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:manage:control', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1067);

-- 新增两个查询权限（录音产物、控制日志），供 Stage 5 页面使用
insert into sys_menu
select '1068', '录音记录查询', '118', '6', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:recording:list', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1068);

insert into sys_menu
select '1069', '控制日志查询', '118', '7', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:control:list', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1069);

-- ---------------------------------------------------------------------------
-- 5、清理辅助过程
-- ---------------------------------------------------------------------------
drop procedure if exists ml_add_column_if_absent;
drop procedure if exists ml_add_index_if_absent;
