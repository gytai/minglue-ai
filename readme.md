# 明略 AI 硬件数据管理平台

基于 [Dash-FastAPI-Admin](https://github.com/gytai/Dash-FastAPI-Admin) 二次开发的明略硬件设备管理系统。后端采用 FastAPI + SQLAlchemy，管理端采用 Dash + feffery-antd-components。

## 契约与差距基线

厂商接口契约盘点与现状差距分析见 [`docs/minglue-contract-and-gap-analysis.md`](docs/minglue-contract-and-gap-analysis.md)。该文档是后续所有阶段的契约基线，记录了 4 篇厂商说明、6 个厂商接口、8 类回调的字段字典，以及逐项差距矩阵与 P0/P1/P2 排序。

后续开发前请先阅读该文档的 §5.8（优先级汇总）与 §6（仍需真实环境确认的事项）。

## 已实现功能

- 设备台账：设备新增、编辑、删除、搜索和分页查询。
- 运行状态：在线/离线/停用、绑定状态、电量、信号、固件版本和最后在线时间。
- 厂商设备控制：批量同步状态/配置、开始和停止录音、查询指令接收结果。
- 完整心跳：录音、存储、电压、电流、充电、USB、磁盘、芯片、IP 和待上传录音数。
- 回调接入：接受任意 JSON 回调，保留原始报文并自动提取常见设备字段。
- 自动建档：未知设备首次上报时自动创建设备记录。
- 幂等处理：存在事件 ID 时自动识别重复回调。
- 回调审计：按设备、事件类型、处理结果、重复标记和时间范围查询原始回调日志，详情展示幂等键与处理信息，报文预览自动脱敏。
- 安全校验：配置回调密钥后支持 HMAC-SHA256 签名校验。
- 权限菜单：设备管理和回调日志均接入现有菜单、角色与按钮权限体系。
- 双数据库脚本：同时提供 MySQL 和 PostgreSQL 初始化 SQL。

## 回调接口

```text
POST /open/minglue/callback
POST /open/minglue/callback/{event_type}
Content-Type: application/json
```

回调接收已覆盖 `rec`、`op`、`sys`、`reclist`、`log`、`fc`、`asr` 和设备心跳，成功响应为 `{"code": 0}`。**注意**：其中 `upload`（`logType` 为 `log`/`rec`、无顶层 `type`）的类型归一化、幂等键策略与处理失败时的原始报文留存仍为待办，详见契约文档 §5.3 的 B6/B11/B12。同时保留兼容模式，支持从顶层、`content` 列表或 `data`、`body`、`payload`、`device` 对象中识别以下字段：

| 语义 | 兼容字段 |
| --- | --- |
| 设备编码 | `device_code`、`deviceCode`、`device_id`、`deviceId`、`deviceSn`、`sn`、`imei` |
| 事件 ID | `event_id`、`eventId`、`message_id`、`messageId`、`requestId` |
| 事件类型 | `event_type`、`eventType`、`type`、`action`、`topic` |
| 事件时间 | `event_time`、`eventTime`、`timestamp`、`time`、`occurTime` |

心跳会按厂商字段更新设备台账，包括 `remain_power`、`rssi`、`update_time`、`version`、`record_status`、`remain_storage`、`battery_voltage`、`battery_current`、`charged_status`、`usb_status`、`disk_mount_status`、`rectd`、`recnu` 和 `nm`。

若设置环境变量 `MINGLUE_CALLBACK_SECRET`，调用方需发送 `X-Signature` 或 `X-Callback-Signature` 请求头，值为原始请求体的 HMAC-SHA256 十六进制摘要（可带 `sha256=` 前缀）。

示例：

```bash
curl -X POST http://127.0.0.1:9099/open/minglue/callback/online \
  -H 'Content-Type: application/json' \
  -d '{"eventId":"evt-001","deviceSn":"ML-0001","battery":86,"timestamp":1789992000000}'
```

## 本地启动

项目要求 Python 3.9+、MySQL 5.7+ 或 PostgreSQL，以及 Redis。

```bash
pip3 install -r requirements.txt
```

1. 在数据库中执行 `dash-fastapi-backend/sql/dash-fastapi.sql`；PostgreSQL 使用 `dash-fastapi-pg.sql`。
2. 修改前后端各自的 `.env.dev` 数据库、Redis 和服务地址配置。
3. 分别启动后端和前端：

```bash
cd dash-fastapi-backend
python3 app.py --env=dev

cd ../dash-fastapi-frontend
python3 app.py --env=dev
```

默认管理端地址为 `http://127.0.0.1:8088`，默认账号为 `admin / admin123`，后端 OpenAPI 地址为 `http://127.0.0.1:9099/docs`。

## 明略 AIOT 接口配置

设备状态同步和录音控制会先调用 `/thiea/site/accountLogin` 获取 token，密码按照厂商要求使用 AES-128-ECB + PKCS7 加密。配置以下环境变量：

```text
MINGLUE_API_BASE_URL=https://aiot-dev.mlamp.cn
MINGLUE_API_USERNAME=厂商分配的用户名
MINGLUE_API_PASSWORD=明文密码（仅保存在安全环境变量中）
MINGLUE_API_AES_KEY=厂商提供的16字节密钥
MINGLUE_API_AUTH_SCHEME=Bearer
MINGLUE_API_TIMEOUT=10
```

若现场接口要求直接传 token 而不是 `Bearer token`，将 `MINGLUE_API_AUTH_SCHEME` 设置为空字符串。

### 回调接入（`POST /open/minglue/callback`）

回调接收地址由接入方自行提供，厂商文档未规定路径；除心跳外 7 类回调的成功响应体也
未定义，本系统统一返回 `{"code": 0}`。另有三个回调侧开关：

```text
# 本系统加固用的 HMAC-SHA256 签名密钥。厂商未定义回调签名，此处不是厂商标准：
# 留空 = 完全不校验（默认，也是生产必须保持的状态）；设置后接受
# X-Signature / X-Callback-Signature = hex(hmac_sha256(secret, 原始请求体))，
# 校验失败返回 401 并落 signature_valid='N' 的拒绝记录。
MINGLUE_CALLBACK_SECRET=

# 同步落库预算（秒）。厂商规定"回调地址响应超过 3s 即视为失败"，默认 2.5s 预留写出
# 余量。超时后回滚业务改动、落一条 process_status='timeout' 的审计记录（原始报文
# 完整保留，可重放）并返回 503 供厂商重试；置 0 或负值关闭超时保护（仅排障用）。
MINGLUE_CALLBACK_RESPONSE_BUDGET=2.5

# 厂商墙钟时间字段（如 update_time）的时区归属。落库口径统一为 UTC naive：Unix 秒
# 按 UTC 换算，带偏移的 ISO 串按偏移换算，不带偏移的墙钟串按本变量换算（留空 =
# 原样保存）。厂商文档未说明是否为北京时间，确认为 Asia/Shanghai 时只改这里。
MINGLUE_VENDOR_TIMEZONE=
```

## 目录说明

```text
dash-fastapi-backend/module_device/       设备、回调后端模块
dash-fastapi-frontend/views/device/       设备管理与回调日志页面
dash-fastapi-frontend/callbacks/device_c/ 页面交互回调（device_page_logic.py 为纯逻辑层）
dash-fastapi-frontend/api/device.py       前端 API 封装
dash-fastapi-frontend/tests/              前端回调/组件测试
```

厂商接口对接代码位于 `module_device/service/minglue_api_service.py`，回调字段映射位于 `module_device/service/device_service.py`，厂商回调入口（路由、签名加固、响应时限）位于 `module_device/controller/callback_controller.py`。

## 测试

```bash
# 后端：数据模型、DAO、迁移、回调字段映射，以及 8 类回调的请求级测试
#       （把回调路由挂到最小 FastAPI 应用上发真实请求，内存 SQLite 装配，无需真实数据库）
cd dash-fastapi-backend
python3 -m pytest

# 前端：页面纯逻辑、设备/回调回调、页面组件渲染与权限显隐，
#       以及"前端按钮权限 ↔ 后端接口守卫 ↔ MySQL/PostgreSQL 种子数据"的一致性
cd dash-fastapi-frontend
python3 -m pytest
```

前端测试用轻量组件桩替换 `dash` / `feffery-antd-components`，因此在未安装前端运行时依赖的环境同样可运行；它覆盖回调层逻辑与页面结构，**不替代**浏览器端人工验收。
