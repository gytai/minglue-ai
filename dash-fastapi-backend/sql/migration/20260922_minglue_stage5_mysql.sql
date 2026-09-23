-- ---------------------------------------------------------------------------
-- 明略后台 Stage 5（管理端页面、交互与权限）迁移脚本 — MySQL
--
-- 背景：Stage 2 已经建好设备模块的 9 条菜单/按钮权限，但"设备控制"（1067）、
--       "录音记录查询"（1068）、"控制日志查询"（1069）没有分配给默认普通角色
--       （role_id = 2）。Stage 5 的设备页面把状态/配置同步、开始/停止录音、
--       指令结果查询都接入到这些权限上：前端按钮显隐与后端接口守卫使用同一组
--       权限标识，因此必须让角色在两层都拿到同样的授权，否则"前端看得见、
--       后端 403"或"功能对非超管不可用"。
--
-- 本脚本可重复执行：菜单用 menu_id 判重，角色关联用 (role_id, menu_id) 判重，
-- 不会产生重复记录，也兼容尚未执行 Stage 2 迁移（菜单缺失）的库。
-- ---------------------------------------------------------------------------

-- 1、补齐菜单/按钮权限（与 dash-fastapi.sql 的 ID 一致，缺失才插入）
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

insert into sys_menu
select '1068', '录音记录查询', '118', '6', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:recording:list', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1068);

insert into sys_menu
select '1069', '控制日志查询', '118', '7', '#', '', '', '', 1, 0, 'F', '0', '0',
       'device:control:list', '#', 'admin', sysdate(), '', null, ''
where not exists (select 1 from sys_menu where menu_id = 1069);

-- 2、把设备模块权限授予默认普通角色（role_id = 2），与 dash-fastapi.sql 保持一致
insert into sys_role_menu (role_id, menu_id)
select seed.role_id, seed.menu_id
from (
    select '2' as role_id, '118' as menu_id union all
    select '2', '119' union all
    select '2', '1061' union all
    select '2', '1062' union all
    select '2', '1063' union all
    select '2', '1064' union all
    select '2', '1065' union all
    select '2', '1066' union all
    select '2', '1067' union all
    select '2', '1068' union all
    select '2', '1069'
) as seed
where not exists (
    select 1 from sys_role_menu rm
    where rm.role_id = seed.role_id and rm.menu_id = seed.menu_id
);
