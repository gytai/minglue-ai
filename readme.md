# 明略 AI 硬件数据管理平台

基于 [Dash-FastAPI-Admin](https://github.com/gytai/Dash-FastAPI-Admin) 二次开发的明略硬件设备管理系统。后端采用 FastAPI + SQLAlchemy，管理端采用 Dash + feffery-antd-components。

## 契约与差距基线

厂商接口契约盘点与现状差距分析见 [`docs/minglue-contract-and-gap-analysis.md`](docs/minglue-contract-and-gap-analysis.md)。该文档是全部阶段的契约基线，记录 4 篇厂商说明、6 个厂商接口、8 类回调的字段字典、逐项差距矩阵、P0/P1/P2 排序，以及 Stage 6 的全链路验收结论与修复记录。

后续开发前请先阅读该文档的 §5.8（优先级汇总）、§6（仍需真实环境确认的事项）与 §10（全链路验收结论）。

## 已实现功能

- 设备台账：设备新增、编辑、删除、搜索和分页查询。
- 运行状态：在线/离线/停用、绑定状态、电量、信号、固件版本和最后在线时间。
- 厂商设备控制：批量同步状态/配置、开始和停止录音、查询指令接收结果。
- 完整心跳：录音、存储、电压、电流、充电、USB、磁盘、芯片、IP 和待上传录音数。
- 回调接入：接受 8 类厂商回调，保留原始报文并自动提取设备字段。
- 自动建档：未知设备首次上报时自动创建设备记录。
- 幂等处理：每条回调都有确定性的非空幂等键，重复推送只留 `duplicate_flag='Y'` 审计记录。
- 回调审计：按设备、事件类型（精确匹配）、处理结果、重复标记和时间范围查询原始回调日志，详情展示幂等键与处理信息，报文预览自动脱敏。
- 安全校验：可选 HMAC-SHA256 回调签名加固（默认关闭）。
- 权限菜单：设备管理、回调日志、录音产物、控制日志均接入现有菜单、角色与按钮权限体系。
- 双数据库脚本：同时提供 MySQL 和 PostgreSQL 的初始化 SQL 与增量迁移 SQL。

## 部署与升级

### 环境要求

Python 3.10+（已在 3.12 验证）、MySQL 5.7+ 或 PostgreSQL 9.6+、Redis。

```bash
# MySQL
pip3 install -r requirements.txt

# PostgreSQL（把 asyncmy/PyMySQL 换成 asyncpg/psycopg2）
pip3 install -r requirements-pg.txt
```

> PostgreSQL 下 `psycopg2` 需要系统存在 `libpq`（APScheduler 的 job store 走同步驱动）。
> macOS + Homebrew 若报 `Library not loaded: libpq.5.dylib`，把 libpq/openssl 的目录加入
> `DYLD_LIBRARY_PATH`（例如 `/opt/homebrew/lib/postgresql@14` 与
> `/opt/homebrew/opt/openssl@3/lib`），或改用 `psycopg2-binary`。

### 全新安装

1. **建库并初始化表结构与种子数据**

   ```bash
   # MySQL：必须显式指定 utf8mb4，否则客户端按 latin1 解析中文种子数据，
   # sys_notice 会因 varchar(50) 超长而中断，导致后续所有 ml_* 表根本没被创建
   mysql --default-character-set=utf8mb4 -u<user> -p<pwd> <db> < dash-fastapi-backend/sql/dash-fastapi.sql

   # PostgreSQL
   psql -U <user> -d <db> -f dash-fastapi-backend/sql/dash-fastapi-pg.sql
   ```

2. **配置环境变量**：复制 `dash-fastapi-backend/.env.dev` 与 `dash-fastapi-frontend/.env.dev`，
   按「环境变量参考」一节填写数据库、Redis、服务地址与厂商凭证。生产环境请从密钥管理系统
   注入敏感值，不要写进仓库。

3. **启动**

   ```bash
   # 后端（默认 9099）
   cd dash-fastapi-backend && python3 app.py --env=dev

   # 前端（默认 8088）
   cd dash-fastapi-frontend && python3 app.py --env=dev
   ```

   生产建议用 WSGI/ASGI 服务器托管：后端 `uvicorn app:app`、前端 `waitress-serve wsgi:app`。

默认管理端地址 `http://127.0.0.1:8088`，默认账号 `admin / admin123`（**首次登录后必须修改**），
后端 OpenAPI 地址 `http://127.0.0.1:9099/docs`。

> 若系统参数 `sys.account.captchaEnabled` 为 `true`（默认），登录需要图形验证码；
> 自动化验收可临时把它置为 `false`：`redis-cli -n <db> set sys_config:sys.account.captchaEnabled false`。
> 注意该键在后端每次启动时会按数据库里的 `sys_config` 重新写入，需要**在启动后**设置。

### 从旧版本升级（Stage 1 及更早结构）

升级脚本只做「加列 / 加表 / 加索引 / 加菜单」，不删除既有列与数据，且可重复执行。

```bash
# MySQL
mysql --default-character-set=utf8mb4 -u<user> -p<pwd> <db> < dash-fastapi-backend/sql/migration/20260922_minglue_stage2_mysql.sql
mysql --default-character-set=utf8mb4 -u<user> -p<pwd> <db> < dash-fastapi-backend/sql/migration/20260922_minglue_stage5_mysql.sql

# PostgreSQL
psql -U <user> -d <db> -f dash-fastapi-backend/sql/migration/20260922_minglue_stage2_pg.sql
psql -U <user> -d <db> -f dash-fastapi-backend/sql/migration/20260922_minglue_stage5_pg.sql
```

升级后建议按「验收与回归」一节的命令核验：升级库与新建库的 `ml_*` 表结构、索引、
菜单与角色授权应完全一致。

## 环境变量参考

### 后端通用（`dash-fastapi-backend/.env.dev` / `.env.prod`）

| 变量 | 说明 |
| --- | --- |
| `APP_ENV` / `APP_HOST` / `APP_PORT` / `APP_ROOT_PATH` | 运行环境、监听地址、端口、代理前缀（生产常为 `/prod-api`） |
| `JWT_SECRET_KEY` / `JWT_EXPIRE_MINUTES` | 登录令牌密钥与有效期，生产必须替换 |
| `DB_TYPE` | `mysql` 或 `postgresql` |
| `DB_HOST` / `DB_PORT` / `DB_USERNAME` / `DB_PASSWORD` / `DB_DATABASE` | 数据库连接 |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_USERNAME` / `REDIS_PASSWORD` / `REDIS_DATABASE` | Redis 连接 |
| `MINGLUE_CALLBACK_SECRET` | 回调加固签名密钥，**默认留空 = 不校验**，生产应保持为空（详见下节） |
| `MINGLUE_CALLBACK_RESPONSE_BUDGET` | 回调同步落库预算（秒），默认 2.5（厂商 3s 判失败），≤0 关闭 |
| `MINGLUE_VENDOR_TIMEZONE` | 厂商墙钟时间字段时区，留空 = 原样保存；确认是北京时间则填 `Asia/Shanghai` |
| `MINGLUE_API_BASE_URL` | 厂商 AIOT 服务地址，生产置空表示未配置 |
| `MINGLUE_API_USERNAME` / `MINGLUE_API_PASSWORD` / `MINGLUE_API_AES_KEY` | 厂商账号与 16 字节 AES 密钥（密码按 AES-128-ECB + PKCS7 加密后发送） |
| `MINGLUE_API_AUTH_SCHEME` | 鉴权头前缀，默认 `Bearer`；置空则发送裸 token |
| `MINGLUE_API_BATCH_SN_QUERY` | 批量接口是否同时发送 query `sn`，默认 `true` |
| `MINGLUE_API_TIMEOUT` | 厂商接口超时（秒），默认 10 |

### 前端（`dash-fastapi-frontend/.env.dev` / `.env.prod`）

| 变量 | 说明 |
| --- | --- |
| `APP_BASE_URL` | 后端地址，前端服务端调用使用 |
| `APP_IS_PROXY` / `APP_PROXY_PATH` | 是否走后端代理及代理前缀 |
| `APP_SECRET_KEY` / `APP_HOST` / `APP_PORT` / `APP_DEBUG` | Flask/Dash 侧配置，生产需关 debug 并替换密钥 |

## 回调接口

```text
POST /open/minglue/callback
POST /open/minglue/callback/{event_type}
Content-Type: application/json
```

回调接收已覆盖 `rec`、`op`、`sys`、`reclist`、`upload`（`logType` 为 `log`/`rec`、无顶层
`type`）、`fc`、`asr` 和设备心跳，成功响应统一为 `{"code": 0}`（心跳是厂商文档唯一明确定义
响应体的一类，其余沿用同一响应体，便于真实联调时观察厂商是否重推）。

回调接收地址由接入方自行提供，厂商文档未规定路径，因此 `/open/minglue/callback` 属于合理
设计而非契约偏差。

### 接入步骤

1. 在本系统部署一个对公网（或厂商可达网络）暴露的回调地址，路径用上面两个之一。
2. 在厂商后台按类别配置回调地址（厂商支持配置多个分类别地址）。若只想用单一入口，
   用不带 `{event_type}` 的路径即可，类型由报文里的 `type`/`logType`/心跳特征自动识别。
3. 确保 `MINGLUE_CALLBACK_SECRET` 为空（厂商不签名，开启会让全部真实回调 401）。
4. 让厂商侧回调延迟与频率落在契约范围内：心跳约每 3 分钟一次，回调响应必须 3 秒内返回。
5. 联调时按「联调清单」逐项核对。

兼容字段（报文结构变动时的兜底）：设备编码支持 `device_code`、`deviceCode`、`device_id`、
`deviceId`、`deviceSn`、`sn`、`imei`；事件 ID 支持 `event_id`、`eventId`、`message_id`、
`messageId`、`requestId`；事件类型支持 `event_type`、`eventType`、`type`、`action`、`topic`；
事件时间支持 `event_time`、`eventTime`、`timestamp`、`time`、`occurTime`。

心跳会按厂商字段更新设备台账，包括 `remain_power`、`rssi`、`update_time`、`version`、
`record_status`、`remain_storage`、`battery_voltage`、`battery_current`、`charged_status`、
`usb_status`、`disk_mount_status`、`rectd`、`recnu` 和 `nm`。

### 回调签名加固（可选，非厂商标准）

厂商文档**未定义**任何回调签名机制。`MINGLUE_CALLBACK_SECRET` 只是接入方自选的加固手段：

- 留空（默认，也是生产必须保持的状态）：完全不校验，任何报文照常接收。
- 设置后：接受 `X-Signature` 或 `X-Callback-Signature`，值为
  `hex(hmac_sha256(secret, 原始请求体))`，可带 `sha256=` 前缀；缺失或不匹配返回 `401`
  并**先**落一条 `signature_valid='N'`、`process_status='rejected'` 的拒绝记录。
- 只有在接入网关/反向代理确实在本入口前对原始请求体签名时才可开启。

### 回调响应与失败重试

| 场景 | 响应 | 落库 |
| --- | --- | --- |
| 处理成功 | `200 {"code": 0}` | `process_status='success'` |
| 重复推送 | `200 {"code": 0}` | 新增 `duplicate_flag='Y'` 记录，不重复改设备状态 |
| 业务处理失败 | `500` | 业务改动回滚 + 独立事务落 `process_status='failed'`（含原始报文与原因） |
| 落库超时（超预算） | `503 {"code": 1}` | 业务改动回滚 + 落 `process_status='timeout'`（原始报文完整，可重放） |
| 签名校验失败 | `401` | 落 `signature_valid='N'`、`process_status='rejected'` |

所有非成功路径都会先留下完整原始报文，厂商若因非 2xx 不重推，可据
`ml_callback_log.payload_json` 重放。

示例：

```bash
curl -X POST http://127.0.0.1:9099/open/minglue/callback \
  -H 'Content-Type: application/json' \
  -d '{"sn":"MLR432KP42C19568","device_status":1,"record_status":0,"remain_power":40,
       "rssi":31,"update_time":"2026-09-23 19:37:23","version":"5.4.49","online":2}'
```

## 联调清单（真实设备 / 厂商测试环境）

> **前置约束**：不得向真实生产设备或生产厂商环境发送控制命令。真实联调必须使用用户
> 明确授权的测试环境与测试设备。

| # | 待确认项 | 操作 | 通过标准 |
| --- | --- | --- | --- |
| 1 | 鉴权头形态（U1） | 真实环境调一次 `status/batch` | 200 且 `code=0`；否则把 `MINGLUE_API_AUTH_SCHEME` 置空重试 |
| 2 | 批量接口参数位置（U2） | 保持默认（query + body 都发）调一次 | 200；若报参数错误，置 `MINGLUE_API_BATCH_SN_QUERY=false` 只发 body |
| 3 | 心跳墙钟时区（U12） | 取一次真实心跳，与设备本地时间/`curl` 打点比对 | 一致则 `MINGLUE_VENDOR_TIMEZONE` 填 `Asia/Shanghai`，否则保持空 |
| 4 | 回调重推策略（U10/U14） | 观察厂商对 401/503/非 `{"code":0}` 的反应 | 记录是否重推、间隔；不重推则按 `payload_json` 重放 |
| 5 | `_eof` 分片标记位置（U13） | 取一份真实分片文件名 | 确认标记在扩展名之前或之后，收紧识别规则 |
| 6 | `config_data` 结构（U3） | 取一次真实 `config/status/batch` 返回 | 拿到字段定义后决定是否结构化建模 |
| 7 | `nm` 类型（U4） | 真实心跳核对 | 确认字符串或整型，必要时调整映射 |
| 8 | `charged_status` 满电取值（U5） | 真实心跳/日志核对 | 确认 `finished` 还是 `finish`，必要时统一 |
| 9 | `upload.status` / `fc.status` / `asr.status` 取值域（U6–U8） | 向厂商索取状态表 | 补齐枚举后可做校验与前端文案 |
| 10 | 指令日志 `payload`/`response` 结构（U9） | 向厂商索取 | 补齐后决定是否结构化展示 |
| 11 | 业务错误码表（U11） | 向厂商索取 | 把 `code != 0` 映射成更精确的提示 |
| 12 | 厂商示例 AES 密钥敏感级别 | 向厂商确认 | 若确认为公开示例，可回填为显式文档常量（无必要） |
| 13 | 心跳落库压力 | 按真实设备规模评估 | 规模大时考虑心跳降采样或独立表 |
| 14 | 日志文件下载解析 | 与厂商确认对象存储访问方式 | 决定是否解析 `sys`/`op`/`reclist` 日志文件内容 |

## 验收与回归

### 1. 离线测试（不需要数据库/Redis）

```bash
cd dash-fastapi-backend && python3 -m pytest     # 后端：模型、DAO、迁移、回调与厂商 mock，131 项
cd dash-fastapi-frontend && python3 -m pytest    # 前端：页面逻辑、回调层、权限一致性，77 项
```

前端测试用轻量组件桩替换 `dash` / `feffery-antd-components`，因此在未安装前端运行时依赖的
环境同样可运行；它覆盖回调层逻辑与页面结构，**不替代**浏览器端人工验收。

### 2. 全链路验收（真实 MySQL/PostgreSQL + Redis + 后端进程）

`dash-fastapi-backend/tests/e2e/run_acceptance.py` 会对**运行中的后端**跑 65 项端到端验收，
厂商侧由脚本内置的 mock HTTP 服务顶替（不会连接真实厂商环境）。

```bash
# 1) 起库：MySQL / PostgreSQL + Redis（本地容器或已有实例均可）
# 2) 起后端，并把厂商地址指向 mock（mock 由验收脚本自己拉起，端口 18080）
cd dash-fastapi-backend
export DB_TYPE=mysql DB_HOST=127.0.0.1 DB_PORT=3306 DB_USERNAME=root DB_PASSWORD=<pwd> DB_DATABASE=<db>
export REDIS_HOST=127.0.0.1 REDIS_PORT=6379 REDIS_DATABASE=0
export MINGLUE_API_BASE_URL=http://127.0.0.1:18080
export MINGLUE_API_USERNAME=e2e-user MINGLUE_API_PASSWORD=e2e-password MINGLUE_API_AES_KEY=e2e-test-key-001
python3 -m uvicorn app:app --host 127.0.0.1 --port 9099

# 3) 关掉登录验证码（脚本用固定 code 登录）
redis-cli -n 0 set sys_config:sys.account.captchaEnabled false

# 4) 跑验收
export MINGLUE_E2E_BASE_URL=http://127.0.0.1:9099 MINGLUE_E2E_VENDOR_PORT=18080
export MINGLUE_E2E_ROLE_USER=niangao MINGLUE_E2E_ROLE_PASSWORD=admin123   # 可选：校验普通角色权限
python3 tests/e2e/run_acceptance.py
```

覆盖范围：登录与鉴权、菜单/按钮权限（含普通角色）、8 类回调端到端与幂等、设备 CRUD、
厂商批量状态/配置同步与逐台结果、录音开关与指令回查、回调日志与录音产物接口、
返回值脱敏、厂商客户端行为（token 缓存、鉴权头、AES 密文、批量参数位置）。

### 3. 双库结构与升级路径核对

```bash
# 用 Stage 1 结构建库后依次跑迁移脚本，再与全新初始化库比对 ml_* 表结构、索引、菜单授权
# 两个库应完全一致（列顺序除外）。Stage 6 已按此方法在 MySQL 8.0 与 PostgreSQL 15 上核验通过。
```

## 目录说明

```text
dash-fastapi-backend/module_device/            设备、回调后端模块
dash-fastapi-backend/sql/                      初始化 SQL
dash-fastapi-backend/sql/migration/            增量迁移 SQL（可重复执行）
dash-fastapi-backend/tests/e2e/run_acceptance.py 全链路验收脚本
dash-fastapi-frontend/views/device/            设备管理与回调日志页面
dash-fastapi-frontend/callbacks/device_c/      页面交互回调（device_page_logic.py 为纯逻辑层）
dash-fastapi-frontend/api/device.py            前端 API 封装
dash-fastapi-frontend/tests/                   前端回调/组件测试
```

厂商接口对接代码位于 `module_device/service/minglue_api_service.py`，回调字段映射位于
`module_device/service/device_service.py`，厂商回调入口（路由、签名加固、响应时限）位于
`module_device/controller/callback_controller.py`。

## 已知限制

- **真实设备联调尚未执行**：控制类接口（开始/停止录音）只在 mock 厂商服务上验证过，
  未对任何真实设备下发过指令。`MINGLUE_API_BASE_URL` 等厂商凭证为占位值。
- 回调签名不是厂商标准，只是本系统加固能力；生产必须保持 `MINGLUE_CALLBACK_SECRET` 为空。
- `config_data`、指令日志 `payload`/`response`、以及 `upload`/`fc`/`asr` 的状态取值域
  厂商文档未定义，当前按原样存储，不做字段级校验（见契约文档 §5.9）。
- 心跳每台约 3 分钟一条，且每次都会落一条回调日志；设备规模大时需评估存储与清理策略
  （当前只提供「超 5 分钟未上报自动离线」的兜底，未做降采样）。
- `sys`/`op`/`reclist` 回调的结构化字段在 `object_key` 指向的日志文件里，本系统只留存
  `object_key`，不下载解析日志内容。
- 升级库与新建库的 `ml_*` 表**列顺序**不同（迁移是追加列，初始化是内联定义）。
  列集合、类型、可空性、默认值与索引已核验一致，列顺序不影响 ORM 映射与业务行为。
- `.env.dev` / `.env.prod` 中的数据库口令是上游默认值（`mysqlroot` / `root`），
  部署时必须替换并从密钥管理系统注入。
