-- ============================================================================
-- 明略后台增量迁移脚本（PostgreSQL）— Stage 5（GYTAI-129）
--
-- 适用：从 Stage 2 及更早的库结构升级到 Stage 5 结构。
-- 特性：可重复执行（幂等）。重复执行不会报错，不会产生重复菜单/权限/角色关联。
--
-- 执行方式：
--   psql -U <user> -d <db> -f sql/migration/20260922_minglue_stage5_pg.sql
--
-- 设计说明：
--   1) 迁移只做"加菜单 / 加角色关联"，不删除任何既有列与数据。
--   2) Stage 2 已建好设备模块 9 条权限，但 1067/1068/1069 未分配给默认普通角色
--      （role_id = 2）。Stage 5 的设备页面把状态/配置同步、开始/停止录音、
--      指令结果查询接在这些权限上，前端按钮显隐与后端接口守卫使用同一组标识，
--      因此必须补齐角色授权，避免"前端可见、后端 403"。
--   3) 菜单用 menu_id 判重，角色关联用 (role_id, menu_id) 判重。
--   4) 不写入任何厂商凭证、token 或 Apifox 密码。
-- ============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 1、补齐设备模块菜单/按钮权限（与 dash-fastapi-pg.sql 的 ID 一致）
-- ---------------------------------------------------------------------------
insert into sys_menu (menu_id, menu_name, parent_id, order_num, path, component, query,
                      route_name, is_frame, is_cache, menu_type, visible, status, perms, icon,
                      create_by, create_time, update_by, update_time, remark)
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

-- ---------------------------------------------------------------------------
-- 2、把设备模块权限授予默认普通角色（role_id = 2）
-- ---------------------------------------------------------------------------
insert into sys_role_menu (role_id, menu_id)
select seed.role_id, seed.menu_id
from (values
    (2, 118), (2, 119),
    (2, 1061), (2, 1062), (2, 1063), (2, 1064), (2, 1065), (2, 1066),
    (2, 1067), (2, 1068), (2, 1069)
) as seed(role_id, menu_id)
where not exists (
    select 1 from sys_role_menu rm
    where rm.role_id = seed.role_id and rm.menu_id = seed.menu_id
);

commit;
