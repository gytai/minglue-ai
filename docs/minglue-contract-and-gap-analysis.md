# 明略 AIOT 接口契约盘点与现状差距分析

- **文档版本**：v1.2
- **文档更新时间**：2026-09-22
- **对应 issue**：GYTAI-127（明略后台 1/6：接口规范盘点与现状差距分析）
- **契约基线提交**：`agent/codex/gytai-123` 的 `3626af6` / `27ed2e8` / `18c2a91`
- **事实来源**：父 issue GYTAI-123 指定的 Apifox 分享文档 + 仓库现有代码
- **适用阶段**：Stage 2 ~ Stage 6 均以本文档为契约基线；后续阶段发现契约偏差时，必须回到 Apifox 原文核对并更新本文档

> 说明：本文档只记录契约事实与差距结论，**不包含 Apifox 访问密码、厂商账号或厂商 AES 密钥**。密码仅用于抓取时访问，未写入仓库、配置或日志。

> v1.2（Stage 4）说明：厂商设备控制接口（§4）已按契约精确对接，并补齐远端同步
> 与本地台账的一致性规则（§4.6）与本地设备管理接口清单（§4.7）。
> §5.3 回调侧结论仍以 Stage 2 为准；Stage 3 未交付（见 §9 遗留说明）。

---

## 0. 契约来源与取证方式

### 0.1 来源

| 项 | 值 |
| --- | --- |
| 分享项目 | `AI工牌管理系统` |
| 分享 ID | `e8358739-0da4-4018-8f00-0fee80d288aa` |
| 入口文档 | `doc-7570437`（回调说明文档） |
| 项目 ID | `5966917` |
| 模块 ID | `1018375` |

访问流程（仅记录方法，不含口令）：分享页需要访问密码，通过 `POST /api/v1/shared-doc-auth`（form-urlencoded，`id` + `password`）换取 `sharedDoc` 会话 Cookie，之后才能读取 `doc` / `http-api-tree` / `http-apis` / `data-schemas` 等接口。

### 0.2 文档清单（4 篇说明 + 6 个接口）

| 类型 | 名称 | 文档/接口 ID | 备注 |
| --- | --- | --- | --- |
| doc | 获取 access_token 方式说明 | 6369279 | 登录鉴权、AES 加密、返回结构 |
| doc | 设备日志与心跳说明 | 6401426 | sys / op / reclist 日志字段、心跳 API 返回 |
| doc | 回调说明文档 | 7570437 | 8 类回调报文、心跳回调样例 |
| doc | 心跳数据说明 | 7650014 | 心跳字段字典、状态取值、数据修正逻辑 |
| api | 账号登录 | 291190701 | `POST /thiea/site/accountLogin` |
| api | 批量获取设备状态 | 369053382 | `POST /thiea/hermes/device/status/batch` |
| api | 批量获取设备最新配置状态 | 369053368 | `POST /thiea/hermes/device/config/status/batch` |
| api | 开启设备录音 | 369053381 | `POST /thiea/hermes/device/start/shadow` |
| api | 停止设备录音 | 369053395 | `POST /thiea/hermes/device/stop/shadow` |
| api | 获取设备指令接收日志 | 369053389 | `GET /thiea/hermes/device/cmd/receive/log` |

**重要前提**：厂商 AIOT 设备服务在分享文档中**只有以上 5 个设备接口**，没有"设备列表/设备详情"查询接口。因此本地设备台账只能由 **回调 + 心跳 + 批量状态接口** 构建，不能指望从厂商侧分页拉取设备清单。这是后续所有阶段的设计前提。

---

## 1. 登录与鉴权契约

### 1.1 获取 access_token

- `Method`: **POST**
- `Path`: `/thiea/site/accountLogin`
- `Content-Type`: `application/json`

请求体：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `username` | string | 是 | 分配的服务用户名 |
| `password` | string | 是 | **AES 加密后的密码**，不是明文 |
| `isLock` | boolean | 是 | 固定传 `true` |
| `cid` | string | 否 | 验证码 ID（页面登录用） |
| `code` | string | 否 | 验证码（页面登录用） |

密码加密要求（文档明确）：

- 算法：**AES-128**
- 模式：**ECB**
- Padding：**PKCS7**
- 输出：**Base64**
- 密钥：16 字节，由厂商提供

文档给出的加密向量（用于自检，**示例密钥非本项目真实凭证，Stage 6 需确认其敏感级别**）：

```text
encrypt('xxx', '<16字节示例密钥>') == 'R5V4MqWkJ4kD/zNcqaxPwQ=='
```

成功响应：

```json
{
  "code": 0,
  "message": "操作成功",
  "data": {
    "id": 1,
    "username": "admin",
    "token": "<access_token>",
    "expires": 604800
  },
  "timestamp": 1743487690,
  "traceID": "18f90f47d11c3218e2851d15f540082c"
}
```

`data.expires` 单位为**秒**（示例 604800 = 7 天）。

### 1.2 鉴权头

- 公共参数（`common-parameters`）定义：请求头 `Authorization`，默认值 `{{accessToken}}`，默认启用。
- 但登录文档的代码示例同时出现了 `Authorization: {{accessToken}}` 与 `Authorization: Bearer eyJ...` 两种写法。
- 文档**未定义** securityScheme（`security-schemes` 返回空数组）。

→ 结论：**鉴权头是否带 `Bearer ` 前缀属于"文档不明确"**，必须用真实环境确认（见 §6-1）。

---

## 2. 厂商回调契约（8 类）

所有回调统一约定：

- `Method`: **POST**
- `Content-Type`: **application/json**
- 文档明确说明：**可以提供分类别的多个回调地址**，厂商在设备录音/日志处理结束后推送到配置的地址。
- **接收地址由接入方自行提供**，厂商文档未规定路径 → 本系统自定义 `/open/minglue/callback` 与 `/open/minglue/callback/{event_type}` 属于合理设计，不是契约偏差。

### 2.1 `rec` 录音上传

```json
{
  "content": [
    {
      "csn": "MLR4010GBFGFGZAD",
      "merge_success_time": 1726210271,
      "object_key": "lt4/rec/20240913/MLR4010GBFGFGZAD/1726202557/MLR4010GBFGFGZAD_20240913133237_3_AB_none_300000_10.opus",
      "download_url": "",
      "size": 2400000,
      "sn": "MLR4010GBFGFGZAD",
      "duration": 100000
    }
  ],
  "session_id": 61318,
  "topic_name": "hermes",
  "type": "rec"
}
```

| 字段 | 位置 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `type` | 顶层 | string | 是 | 固定 `rec` |
| `session_id` | 顶层 | int | 是 | webhook id（回调配置 ID） |
| `topic_name` | 顶层 | string | 是 | 固定 `hermes` |
| `content` | 顶层 | array | 是 | 单次可含多设备/多文件 |
| `csn` | content[] | string | 是 | 设备序列号（与 `sn` 同值出现） |
| `sn` | content[] | string | 是 | 设备序列号 |
| `object_key` | content[] | string | 是 | 对象存储路径 |
| `size` | content[] | int | 是 | 文件大小（字节） |
| `duration` | content[] | int | 是 | 音频时长 |
| `merge_success_time` | content[] | int | 否 | Unix 秒 |
| `download_url` | content[] | string | 否 | 资源下载地址，非必传 |

**录音文件名规则（文档明确）**：

```text
[目录前缀]/lt4/rec/[日期]/[序列号]/[整段录音开始时间]/[录音文件名]

[序列号]_[开始时间]_[音轨标记]_[文件信息]_[自定义字段]_[文件长度]_[文件序列号].[后缀]
[序列号]_[开始时间]_[音轨标记]_[文件信息]_[自定义字段]_[文件长度]_[文件序列号]_eof.[后缀]   ← 结束分片
```

- `[整段录音开始时间]`：手动按键开机 / API 开始录音 / 开机自动录音的时间，未结束录音的多个分片中保持一致。
- `[自定义字段]`：**API 开启录音时可携带**，正则 `^[a-z0-9]{2,10}$`，可作为整段录音的自定义会话段标识。
- `_eof` 结尾的分片表示当前录音时段最后一个分片。

### 2.2 `op` 操作日志

```json
{
  "content": [
    {
      "csn": "MLR4010GBFGFGZAD",
      "log_type": "lt4_op_log",
      "merge_success_time": 1726206350,
      "object_key": "lt4/log/20240913/MLR4010GBFGFGZAD/MLR4010GBFGFGZAD_20240913134339_op.log",
      "download_url": "",
      "size": 562,
      "sn": "MLR4010GBFGFGZAD"
    }
  ],
  "session_id": 61317,
  "topic_name": "hermes",
  "type": "op"
}
```

### 2.3 `sys` 系统日志

同 `op` 结构，差异：`type` = `sys`、`log_type` = `lt4_sys_log`、`session_id` 示例 61319、`object_key` 以 `_sys.log` 结尾。

### 2.4 `reclist` 待上传录音列表日志

同 `op` 结构，差异：`type` = `reclist`、`log_type` = `lt4_rec_list_log`、`session_id` 示例 61316、`object_key` 以 `_reclist.log` 结尾。

### 2.5 `upload` 文件上传日志

```json
{
  "updatedAt": "2025-04-07 16:22:55",
  "logType": "log",
  "sn": "MLR431Z08EI3OI9E",
  "objectKey": "lt4/log/MLR431Z08EI3OI9E/20250407/MLR431Z08EI3OI9E_20250407162250_sys.log.file",
  "fileName": "MLR431Z08EI3OI9E_20250407162250_sys.log.file",
  "status": 6
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `updatedAt` | string | 是 | 上传时间，`Y-m-d H:i:s` |
| `logType` | string | 是 | **仅 `log` 和 `rec` 两种**；`rec` 表示音频文件 |
| `sn` | string | 是 | 设备序列号 |
| `objectKey` | string | 是 | 云存储 object_key |
| `fileName` | string | 是 | 文件名 |
| `status` | int | 是 | 示例 6，取值含义文档未定义 |

**关键差异**：该类回调**没有顶层 `type` 字段**，且字段名为 camelCase（`objectKey` / `fileName` / `updatedAt`），与前四类 snake_case 风格不同。

### 2.6 `fc` 转码结果（增值服务）

```json
{
  "file_count": 1,
  "files": [
    {
      "download_url": "https://...<带签名>",
      "file_path": "lt4/rec/20251021/MLR431P12MYMSWD3/fc/MLR431P12MYMSWD3_20251021105140_20251021110055_1761015175_3_AB_none_555000_cuted_v2_stereo.mp3"
    }
  ],
  "group_key": "recordid_xxxx",
  "sn": "MLR431P12MYMSWD3",
  "status": "completed",
  "task_id": "1tsk3w11000ddqkwe4tsgok4mfifqify",
  "timestamp": 1761539510,
  "type": "fc"
}
```

- `status` 为**字符串**（示例 `completed`），与 `upload` 的 `status` 为 int 不一致。
- `task_id`：api 提交的裁剪任务会携带对应 task_id。
- `download_url` 带有效期签名。

### 2.7 `asr` 语音识别（增值服务）

```json
{
  "asrTextResultList": [
    { "content": "你不是吗？", "endMs": 10960, "role": 0, "roleMark": 0, "startMs": 4060 }
  ],
  "asrTskId": "2025108a682fcb767893cb347615eb14d5f11e",
  "extraParam": "",
  "objectKey": "lt4/rec/20251028/OEM432OAY2IE23/fc/OEM432OAY2IE23_20251028171443_3_AB_none_300000_43_stereo.mp3",
  "session_id": 888,
  "sn": "OEM432OAY2IE23",
  "status": 100,
  "theiaTaskId": "fc",
  "topic_name": "hermes",
  "type": "asr"
}
```

变体（文档给出第二个样例）额外包含：

- `asrTextObjectKey`：`lt4/rec/20260621/MLR431SSTGA8CZWE/asr/asr-result.json`
- `download_url`：带签名的音频地址
- `theiaTaskId`：示例 `20260621-14cd45e2e0b0`（非固定值）

`asrTextResultList[]` 字段：`content`(string)、`startMs`(int)、`endMs`(int)、`role`(int)、`roleMark`(int)。`role` 用于区分说话人（样例中出现 0/1/2）。

### 2.8 设备心跳（回调）

```json
{
  "sn": "MLR432KP42C19568",
  "device_status": 1,
  "record_status": 0,
  "remain_power": 40,
  "remain_storage": 14833,
  "rssi": 31,
  "update_time": "2025-11-01 19:37:23",
  "version": "5.4.49",
  "key_status": 1,
  "usb_status": 1,
  "battery_voltage": 3955,
  "battery_current": 420,
  "chip": "2108+511",
  "total_storage": 14950,
  "cip": "10.113.36.200",
  "charged_status": "cccv",
  "disk_mount_status": 0,
  "online": 2,
  "rectd": 0,
  "recnu": 16,
  "tenant_id": 100,
  "nm": "ciqziMsatvo5"
}
```

**这是全部 8 类回调中唯一在文档里明确定义了成功响应的一类**：

```text
Response
STATUS: 200
BODY: { "code": 0 }
```

其他硬性要求（文档明确）：

1. 设备约**每 3 分钟**上报一次心跳。
2. 查询 API 始终返回最新上报数据；**超过 5 分钟没有上报心跳**，再查询设备状态时 `device_status: 0`（离线）。
3. 心跳回调延迟通常在 **1–10s**；回调地址**响应超过 3s 即视为失败**。

---

## 3. 心跳字段字典

来源：`心跳数据说明`（doc 7650014）。

| 字段 | 类型 | 必填 | 说明 | 取值/示例 |
| --- | --- | --- | --- | --- |
| `sn` | string | 是 | 设备序列号 | `MLR432KP42C19568` |
| `device_status` | int | 是 | 设备状态 | 1: 正常在线；0: 离线（超 5 分钟未上报） |
| `record_status` | int | 是 | 录音状态 | 1: 正在录音；0: 不在录音 |
| `remain_power` | int | 是 | 剩余电量百分比 | 0 ~ 100 |
| `remain_storage` | int64 | 是 | 剩余磁盘空间 | 单位 MB |
| `rssi` | int | 是 | 4G 信号强度 | 0~31，越大越好；**99 为无信号** |
| `update_time` | string | 是 | 更新时间 | `Y-m-d H:i:s` |
| `version` | string | 是 | 固件版本 | `5.4.49` |
| `key_status` | int | 是 | 按键状态 | 1: 开机；0: 关机 |
| `usb_status` | int | 是 | USB 状态 | 1: 磁盘挂载在工牌上；0: 挂载在别处 |
| `battery_voltage` | int | 是 | 电池电压 | 毫伏 (mV) |
| `battery_current` | int | 是 | 电池电流 | 毫安 (mA)，正=充电，负=放电 |
| `chip` | string | 是 | 芯片类型 | `2108+511` |
| `total_storage` | int64 | 是 | 总磁盘空间 | 单位 MB |
| `cip` | string | 是 | 设备当前基站 IP | `10.113.36.200` |
| `charged_status` | string | 是 | 充电状态 | `charge-off` 放电 / `cccv` 充电中 / `finished` 充满 |
| `disk_mount_status` | int | 是 | 磁盘挂载状态 | 1: 已挂载；0: 未挂载 |
| `online` | int | 是 | 在线状态 | **2: 在线；1: 开机但可能不在线；0: 离线** |
| `rectd` | int | 否 | 当日录音时长 | 单位秒，可能为空 |
| `recnu` | int | 否 | 当前工牌里未上传的录音数量 | 可能为空 |
| `tenant_id` | int64 | 是 | 租户 ID | `100` |
| `nm` | int64 | 否 | API 开启录音标识；API 开启录音时，录音阶段心跳将包含该标识 | 示例 `ciqziMsatvo5`（**类型标注与示例自相矛盾**） |

### 3.1 厂商侧数据修正逻辑（文档明确，接入方需知悉）

1. **时间修正**：若心跳时间超前于当前时间，厂商会自动修正为当前时间。
2. **充电状态修正**：
   - 设备正在录音且 USB 未插入时，`charged_status` 被修正为 `charge-off`；
   - 设备正在录音且 USB 已插入且 `charged_status` 不是 `charge-off` 时，`record_status` 被修正为 0。

### 3.2 设备日志文件字段（位于日志文件内容，**不在回调报文中**）

`sys` 日志行样例：

```json
{"sn":"OEM432OAY2IE23","v":"5.4.50","time":"2025-10-31 22:50:01","rec":0,"usb":0,"key":0,"mnt":1,"rsoc":88,"vbat":4134,"ibat":-55,"chg":"charge-off","df":14933,"total_df":14950,"chip":"2108+511","ccsq":27,"cip":"10.239.78.237"}
```

| 字段 | 说明 |
| --- | --- |
| `sn` | 设备 SN |
| `v` | 固件版本 |
| `time` | 记录时间 |
| `rec` | 是否在录音中，1/0 |
| `usb` | 是否接入 USB，1/0 |
| `key` | 开关机拨片位置，1=on，0=off |
| `mnt` | 是否挂载磁盘，1=mount，0=umount |
| `rsoc` | 电量百分比 0-100 |
| `vbat` | 电池放电电压 |
| `ibat` | 电池充电电流 |
| `chg` | 充电状态：`charge-off` 放电 / `cccv` 充电 / **`finish`** 充电结束 |
| `df` | 磁盘剩余空间 (MB) |
| `total_df` | 总存储空间 (MB) |
| `chip` | 芯片组版本 |
| `ccsq` | 信号质量 0-31，超过 31 无网络 |
| `cip` | IP 地址 |

`op` 操作日志行样例：

```json
{"ops":"system","act":"start","time":1761641146,"tm":"2025-10-28 16:45:46"}
```

`ops` 取值域（文档完整枚举）：`Ota`、`net`、`system`、`recording`、`ERcopy`、`up`、`4G`、`usb`、`recovery`、`ip`、`cmd`、`timesync`、`endurance`；对应 `act` 取值详见 Apifox `设备日志与心跳说明`。

`reclist` 日志行样例：

```json
{"nm":"MLR431YWH2WUMSF9_20251124173145_1763973279_2_BB_none_60000_57.opus","sz":480000}
```

**要点**：`sys` / `op` / `reclist` 回调报文本身只携带 `object_key`（日志文件路径），上述结构化字段存在于**日志文件内容**中，需要下载并解析日志文件才能获得。

---

## 4. 厂商 HTTP API 契约（5 个设备接口）

统一响应包络（`common-responses`）：

| HTTP | 名称 | 响应体 |
| --- | --- | --- |
| 200 | 成功 | `{ "code": 0, "message": "", "data": {...} }`，`code` **0 代表成功**；`code`/`message`/`data` 均必填 |
| 400 | 参数不正确 | `{ "code": 400, "message": "Invalid input" }` |
| 404 | 记录不存在 | `{ "code": 404, "message": "Not found" }` |

- 除 200 外，400/404 响应体**只有 `code` 与 `message`，没有 `data`**。
- 登录响应额外带 `timestamp` / `traceID`（心跳 API 样例则没有），因此这两个字段是**可选**的。
- **业务错误码**（`code` 非 0 的具体取值）文档未定义。

### 4.1 批量获取设备状态

- `Method`: **POST**
- `Path`: `/thiea/hermes/device/status/batch`
- Header：`Authorization`（必填）
- Query：`sn`（API 定义标注为必填）
- Body：

```json
{ "sns": ["MLR432KP42C19568"] }
```

响应 `data`：

```json
{ "entities": [ { /* DeviceDetailRes */ } ] }
```

`DeviceDetailRes` 字段（22 个，与心跳字段一致并额外包含 `nm`）：

`battery_current`(int)、`battery_voltage`(int)、`charged_status`(string)、`chip`(string)、`cip`(string)、`device_status`(int)、`disk_mount_status`(int)、`key_status`(int)、`nm`(string)、`online`(int)、`recnu`(*int 可空)、`record_status`(int)、`rectd`(*int 可空)、`remain_power`(int)、`remain_storage`(int64)、`rssi`(int)、`sn`(string)、`tenant_id`(*int64 可空)、`total_storage`(int64)、`update_time`(string)、`usb_status`(int)、`version`(string)

### 4.2 批量获取设备最新配置状态

- `Method`: **POST**
- `Path`: `/thiea/hermes/device/config/status/batch`
- Header：`Authorization`（必填）
- Query：`sn`（API 定义标注为必填）
- Body：`{ "sns": ["..."] }`

响应 `data`：

```json
{
  "entities": [
    { "config_data": {}, "last_upload_time": "string", "sn": "string" }
  ]
}
```

`config_data` 在 data-schemas 中引用到一个**空 object 定义**（无字段）→ **配置字段结构文档不明确**，无法据此建模。

### 4.3 开启设备录音

- `Method`: **POST**
- `Path`: `/thiea/hermes/device/start/shadow`
- Header：`Authorization`（必填）
- Query：`sn`（必填，设备序列号）
- Body：`{ "nm": "音频ID" }`（`nm` 非必填）

响应 `data`：

```json
{ "msg_id": "必填，回查会用到", "sn": "设备序列号", "stat": "状态" }
```

`msg_id` 为**必填**字段。

### 4.4 停止设备录音

- `Method`: **POST**
- `Path`: `/thiea/hermes/device/stop/shadow`
- Header：`Authorization`（必填）
- Query：`sn`（必填）
- Body：`{}`（空对象）

响应 `data`：同 4.3（`msg_id` 必填）。

### 4.5 获取设备指令接收日志

- `Method`: **GET**
- `Path`: `/thiea/hermes/device/cmd/receive/log`
- Header：`Authorization`（必填）
- Query：`sn`（必填）、`msg_id`（必填，消息 ID）

响应 `data`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `cmd` | string | 命令 |
| `msg_id` | string | 消息 ID |
| `payload` | object | 请求载荷（结构未定义） |
| `req_time` | string | 请求时间 |
| `resp_time` | string | 响应时间 |
| `response` | object | 响应载荷（结构未定义） |
| `sn` | string | 设备序列号 |
| `status` | int | 0: 初始化；1: 成功；2: 返回出错；3: 返回超时 |

### 4.6 远端同步与本地台账的一致性规则（Stage 4 定稿）

厂商 AIOT 侧没有设备列表接口，**本地台账是唯一可查询的设备主数据**（§0.2）。
因此"远端读"与"本地写"之间必须有确定规则，避免批量操作留下不可解释状态：

| 规则 | 内容 |
| --- | --- |
| R1 逐设备独立事务 | 批量状态/配置同步对每台设备使用 savepoint 并单独提交；单台失败只回滚该台，不影响同批其他设备。 |
| R2 逐台结果可解释 | 响应返回 `results[]`（`sn`/`ok`/`skipped`/`error`）与 `total`/`succeeded`/`failed`/`skipped`/`partial`，前端据此提示"部分失败"。`synced` 字段保留为 `succeeded` 的别名。 |
| R3 缺项有确定结论 | 请求了但厂商响应未包含的 `sn` 记为 `failed`（错误说明"响应未包含该设备"），不会静默消失。 |
| R4 状态同步可建档 | 批量**状态**回包按心跳口径 upsert，厂商已知而本地不存在的设备按回调同策略建档（与 §5.5 D1 一致）。 |
| R5 配置同步不建档 | 批量**配置**回包只更新本地已存在的设备；本地没有该设备时记为 `skipped`，不凭配置回包凭空创造没有主数据的台账记录。 |
| R6 本地状态不被覆盖 | 本地 `disabled` 停用设备不会被回调或状态同步改回在线（`_upsert_device` 保留停用态）。 |
| R7 配置结构不猜 | `config_data` 结构未定义（U3），按原样 JSON 落 `config_json`，不做字段级校验。 |

### 4.7 本地设备管理接口（Stage 4 补齐）

本地接口沿用 Dash-FastAPI-Admin 的登录态与按钮权限（`CheckUserInterfaceAuth`），
响应统一为 `{code, msg, data}`：

| 方法 | 路径 | 权限码 | 说明 |
| --- | --- | --- | --- |
| GET | `/device/list` | `device:manage:list` | 分页/筛选（编码、名称、型号、本地状态、心跳三态 `online_status`、绑定状态、使用人） |
| GET | `/device/detail/{device_id}` | `device:manage:query` | 设备详情（含完整心跳字段与配置快照） |
| POST | `/device` | `device:manage:add` | 新增设备；`device_code` 唯一 |
| PUT | `/device` | `device:manage:edit` | 编辑设备；编码冲突校验排除自身 |
| PUT | `/device/status` | `device:manage:edit` | **状态字段维护**：仅改 `status`（online/offline/disabled）与 `bind_status`（bound/unbound），至少传一项 |
| DELETE | `/device/{device_ids}` | `device:manage:remove` | 删除（逗号分隔主键） |
| POST | `/device/remote/status` | `device:manage:control` | 批量设备状态同步，返回 §4.6 R2 的逐台结果 |
| POST | `/device/remote/config` | `device:manage:control` | 批量配置状态同步，返回 §4.6 R2 的逐台结果 |
| POST | `/device/remote/offline-sweep` | `device:manage:control` | 心跳超时（>5 分钟）离线兜底 |
| POST | `/device/{device_code}/recording/start` | `device:manage:control` | 开启录音；`audio_id` 受 `^[a-z0-9]{2,10}$` 约束 |
| POST | `/device/{device_code}/recording/stop` | `device:manage:control` | 停止录音 |
| GET | `/device/{device_code}/command/{message_id}` | `device:manage:control` | 指令结果查询，返回 `{remote, remote_error, local}` |
| GET | `/device/callback/list` | `device:callback:list` | 回调日志分页（类型/设备/处理结果/重复标记/时间） |
| GET | `/device/callback/{callback_id}` | `device:callback:query` | 回调详情（原始报文） |
| DELETE | `/device/callback/{callback_ids}` | `device:callback:remove` | 删除回调日志 |
| GET | `/device/recording/list` | `device:recording:list` | 录音/转码/ASR 产物分页 |
| GET | `/device/control/list` | `device:control:list` | 控制指令日志分页（含厂商 `msg_id`） |

`GET /device/{device_code}/command/{message_id}` 的语义：厂商侧刚下发指令时可能返回
`404 记录不存在`，此时本地已留存 `msg_id`（契约 C8），因此把厂商错误放在 `remote_error`
返回并附本地控制日志；两边都没有记录才判为不存在。

---

## 5. 契约 → 现状差距矩阵

### 5.1 覆盖状态图例

- **已满足**：实现与文档一致，无需改动
- **部分满足**：方向正确但存在缺口或未覆盖分支
- **缺失**：完全没有实现
- **文档不明确**：文档自身矛盾或未定义，需真实环境确认后才能定稿

### 5.2 登录与鉴权

| # | 契约项 | 现状 | 状态 | 优先级 |
| --- | --- | --- | --- | --- |
| A1 | `POST /thiea/site/accountLogin` 路径与方法 | `minglue_api_service.py:_get_token` 一致 | 已满足 | — |
| A2 | 密码 AES-128-ECB + PKCS7 + Base64 | `encrypt_password` 一致；已用独立 openssl 复算文档向量 `R5V4MqWkJ4kD/zNcqaxPwQ==` 通过 | 已满足 | — |
| A3 | `isLock: true` | 固定传 `true` | 已满足 | — |
| A4 | token 缓存与过期（`expires` 秒） | 进程内缓存 + 提前 60s 刷新；401/403 触发一次强制刷新重试；有请求级测试断言"命中缓存不重复登录"与"401 只重试一次" | 已满足（Stage 4 加固） | — |
| A5 | 鉴权头形态（`Bearer ` 前缀与否） | 默认 `Bearer`，可用 `MINGLUE_API_AUTH_SCHEME` 置空；两种形态均有请求级测试 | **文档不明确**（已可配置验证） | P1 |
| A6 | 响应包络 `{code,message,data}`，`code=0` 成功 | `_unwrap` 已处理；400/404 无 `data` 也能正确抛错 | 已满足 | — |
| A7 | 业务错误码（`code` 非 0 取值表） | 仅透传 `message` | 文档不明确 | P2 |

### 5.3 回调接口

| # | 契约项 | 现状 | 状态 | 优先级 |
| --- | --- | --- | --- | --- |
| B1 | POST + `application/json` 接收 | `POST /open/minglue/callback` 与 `/callback/{event_type}` | 已满足 | — |
| B2 | 心跳成功响应 `{"code":0}` / 200 | 返回 `{'code': 0}` | 已满足 | — |
| B3 | 心跳响应需 3s 内返回 | 同步落库后返回，未见超时保护；高频心跳下存在超时风险 | 部分满足 | P1 |
| B4 | `rec` 类型识别与多设备遍历 | 由 `type` 识别，`content[]` 逐设备 upsert | 已满足 | — |
| B5 | `op` / `sys` / `reclist` 类型识别 | 由 `type` 识别；`log_type`/`csn` 可正确取设备 | 已满足 | — |
| B6 | `upload` 类型识别 | 已归一化：先看 `type`，无 `type` 时按 `objectKey`/`fileName`/`updatedAt` 判定为 `upload`，`logType` 单独落 `log_type` 列 | 已满足（Stage 2） | — |
| B7 | `fc` 类型识别 | 由 `type` 识别；`sn`/`task_id` 可提取 | 已满足 | — |
| B8 | `asr` 类型识别 | 由 `type` 识别；`sn`/`asrTskId` 可提取 | 已满足 | — |
| B9 | 心跳（无 `type`）类型识别 | `_infer_event_type` 依据 `device_status`+`update_time` 判定 | 已满足 | — |
| B10 | 原始报文完整留存 | `payload_json` 存整包 JSON（MySQL `longtext` / PG `text`） | 已满足 | — |
| B11 | 幂等键确定性策略 | `dedup_key`（SHA-256，非空唯一）承担去重；有业务键用业务键，无业务键退化为报文规范化摘要；`rec` 每个 `object_key` 各自一条记录 | 已满足（Stage 2） | — |
| B12 | 处理失败可追踪 | 业务改动回滚后用独立事务补 `failed` 审计记录，原始报文不丢失 | 已满足（Stage 2） | — |
| B13 | 签名/鉴权 | 厂商**未定义**回调签名；现有可选 HMAC-SHA256 属本地加固 | 部分满足 | P1 |
| B14 | 签名校验失败审计 | 校验失败直接 401，`signature_valid` 恒为 `'Y'`，无拒绝记录 | 部分满足 | P1 |
| B15 | `session_id`（webhook id）留存 | 未单独落库 | 缺失 | P2 |
| B16 | `topic_name` 留存 | 未单独落库 | 缺失 | P2 |
| B17 | 其余 7 类回调成功响应体 | 文档只定义了心跳响应；其余统一返回 `{"code":0}` | 文档不明确 | P2 |
| B18 | 事件时间口径一致 | `update_time`（字符串 `Y-m-d H:i:s`）按原样存为 naive datetime；`merge_success_time`/`timestamp`（Unix 秒）经 UTC 转换后去掉 tzinfo。两条路径口径不一致，若厂商字符串时间为北京时间将产生固定时差 | 部分满足 | P1 |
| B19 | `rec` 的 `duration` 字段 | 未建模 | 缺失 | P2 |

#### B6 / B11 的实测复现（本阶段已用桩模块验证）

在不依赖数据库的前提下加载 `CallbackService`，按文档样例逐类喂入，实际输出：

| 文档回调类型 | 实际 `event_type` | 实际 `event_id` | 结论 |
| --- | --- | --- | --- |
| `rec`（多设备 2 条） | `rec` | `lt4/rec/a.opus`（**仅首条**） | B11：第二个设备的 `object_key` 丢失 |
| `op` | `op` | `lt4/log/a_op.log` | 正常 |
| `sys` | `sys` | `lt4/log/a_sys.log` | 正常 |
| `reclist` | `reclist` | `lt4/log/a_reclist.log` | 正常 |
| `upload`（`logType=log`） | **`log`** | `lt4/log/x_sys.log.file` | B6：应为 `upload`，且 `log` 与"日志文件"语义混淆 |
| `upload`（`logType=rec`） | **`rec`** | `lt4/rec/x.opus` | B6：**与真正的 `rec` 音频回调类型冲突**，无法区分 |
| `fc` | `fc` | `t1`（`task_id`） | 正常 |
| `asr` | `asr` | `a1`（`asrTskId`） | 正常 |
| 心跳 | `heartbeat` | **`None`** | B11：唯一索引对 NULL 不去重，心跳无幂等 |

时间解析实测：

| 输入 | 输出 | 说明 |
| --- | --- | --- |
| `"2025-11-01 19:37:23"` | `2025-11-01 19:37:23`（naive，原样） | 未做时区换算 |
| `1726210271`（Unix 秒） | `2024-09-13 06:51:11`（UTC naive） | 已做 UTC 换算 |

→ 确认 B18：两条时间路径口径不一致。

### 5.4 厂商设备控制接口

| # | 契约项 | 现状 | 状态 | 优先级 |
| --- | --- | --- | --- | --- |
| C1 | 批量设备状态：路径/方法/Body `{sns}` | 路径/方法/Body 与文档一致，并有请求级测试断言 | 已满足 | — |
| C2 | 批量设备状态：Query `sn` 必填 | 默认同时发送 query `sn`（=首个序列号）与 body `sns`；可用 `MINGLUE_API_BATCH_SN_QUERY=false` 只发 body，便于真实环境试送 | **文档不明确**（已可配置验证） | P1 |
| C3 | 批量配置状态：路径/方法/Body | 与文档一致并有请求级测试 | 已满足 | — |
| C4 | 配置状态结果持久化 | `sync_remote_configs` 逐台写入 `config_json`/`config_last_upload_time`/`config_synced_at`；本地无此设备时跳过而非建档（§4.6 R5） | 已满足（Stage 4） | — |
| C5 | 开启录音：路径 + Query `sn` + Body `nm` | 与文档一致；`nm` 校验 `^[a-z0-9]{2,10}$`；不带 `nm` 时 body 为空对象 | 已满足 | — |
| C6 | 停止录音：路径 + Query `sn` + 空 Body | 与文档一致并有请求级测试 | 已满足 | — |
| C7 | 指令接收日志：路径/方法/Query `sn`+`msg_id` | 与文档一致；`status` 归一为整数，非整数报可读错误 | 已满足 | — |
| C8 | `msg_id` 回传可用于回查 | 开启/停止录音响应强制要求 `msg_id`（缺失即报错），并写入 `ml_device_control_log.msg_id`；`/device/{code}/command/{msg_id}` 合并厂商与本地结果 | 已满足（Stage 4） | — |
| C9 | 批量同步部分失败行为 | 逐设备 savepoint + 独立提交，返回逐台 `results` 与 `partial`；缺项序列号补确定结论（§4.6 R1~R3） | 已满足（Stage 4） | — |
| C10 | 网络超时 / 401 / 403 / 非法响应错误映射 | 超时、网络错误、非 2xx、非法 JSON、非对象包络、缺 `code`、缺 `entities`/`msg_id` 均转可读 `ServiceException`；401/403 强制刷新 token 后**只重试一次**，其余错误不重试 | 已满足（Stage 4） | — |
| C11 | 不泄露密码/token/敏感报文 | 错误信息不回显请求报文；响应片段先按值抹除 token/密码/AES 密钥，再按形态抹除 JWT 与长 Base64，并截断 200 字符；控制日志不记录 token | 已满足（Stage 4） | — |

### 5.5 数据模型与 SQL

| # | 契约项 | 现状 | 状态 | 优先级 |
| --- | --- | --- | --- | --- |
| D1 | 设备主表字段对齐心跳字段 | `ml_device` 覆盖 `sn`(device_code)、`record_status`、`remain_power`(battery_level)、`remain_storage`、`total_storage`、`rssi`(signal_strength)、`update_time`(last_seen_time)、`version`(firmware_version)、`key_status`、`usb_status`、`battery_voltage`、`battery_current`、`chip`、`cip`(ip_address)、`charged_status`、`disk_mount_status`、`online`(status)、`rectd`(today_record_seconds)、`recnu`(pending_recordings)、`tenant_id`、`nm`(audio_id) | 已满足 | — |
| D2 | 在线状态三态语义 | 新增 `online_status` 原样保存 2/1/0，`status` 保留本地语汇 | 已满足（Stage 2） | — |
| D3 | 超 5 分钟未上报即离线 | `DeviceService.mark_stale_devices_offline` 提供超时兜底，仅处理 `online` 设备 | 已满足（Stage 2） | — |
| D4 | 回调日志表字段 | `event_id`/`event_type`/`device_code`/`event_time`/`received_at`/`signature_valid`/`process_status`/`payload_json`/`error_message`/`request_ip` | 已满足 | — |
| D5 | 回调幂等唯一约束 | `uk_ml_callback_dedup_key` 建在**非空**列上，可空列 NULL 不去重的漏洞消除 | 已满足（Stage 2） | — |
| D6 | 录音文件 / 转码结果 / ASR 文本持久化 | 新增 `ml_recording_file`（26 列）承载 duration/转码/ASR | 已满足（Stage 2） | — |
| D7 | 配置状态字段承载 | 新增 `config_json`/`config_last_upload_time`/`config_synced_at`，`sync_remote_configs` 落库 | 已满足（Stage 2） | — |
| D8 | MySQL / PG 脚本等价 | 两套脚本表结构、唯一键、索引等价（`payload_json` longtext/text 差异属方言正常） | 已满足 | — |
| D9 | 菜单与权限种子数据 | 两套脚本均含 `device:manage:*`、`device:callback:*`、`device:manage:control` 共 9 条权限 | 已满足 | — |
| D10 | 不保存厂商明文凭证 | 仓库无 Apifox 密码；README 中密码为占位符；**但文档示例 AES 密钥被写入测试向量**（见 §6-4） | 需确认 | P1 |

### 5.6 前端

| # | 契约项 | 现状 | 状态 | 优先级 |
| --- | --- | --- | --- | --- |
| E1 | 设备列表（搜索/分页/状态/详情/增删改） | `views/device/manage` + `callbacks/device_c/manage_c.py` 完整 | 已满足 | — |
| E2 | 设备状态同步入口 | 已接入 `sync_status` | 已满足 | — |
| E3 | 开启/停止录音入口 | 已接入 `start_recording` / `stop_recording` | 已满足 | — |
| E4 | 配置状态同步入口 | `api/device.py` 已封装 `get_config_status`，**页面未接入** | 缺失 | P1 |
| E5 | 指令结果查询入口 | `api/device.py` 已封装 `get_command_log`，**页面未接入** | 缺失 | P1 |
| E6 | 完整心跳字段展示 | 列表仅展示 code/name/model/firmware/status/bind/owner/phone/department/battery/record/signal/storage/last_seen/remark；**未展示** `charged_status`、`key_status`、`usb_status`、`disk_mount_status`、`chip`、`ip_address`、`today_record_seconds`、`pending_recordings`、`tenant_id`、`audio_id`、`battery_voltage`、`battery_current` | 部分满足 | P1 |
| E7 | 回调日志按设备/类型/时间/结果查询 | 已支持 `event_type`/`device_code`/`process_status`/`begin_time`/`end_time` | 已满足 | — |
| E8 | 回调原始报文格式化查看 | 详情弹窗解析并格式化 `payload_json` | 已满足 | — |
| E9 | 页面不泄露密钥/token | 未展示签名密钥或 token | 已满足 | — |
| E10 | 加载/部分失败/超时/无权限反馈 | 控制操作有基础反馈；无"部分失败"专用反馈（后端也不返回逐设备结果） | 部分满足 | P1 |
| E11 | 危险操作二次确认 | 删除有确认；停止录音未见明确确认 | 部分满足 | P2 |

### 5.7 测试

| # | 契约项 | 现状 | 状态 | 优先级 |
| --- | --- | --- | --- | --- |
| F1 | AES 加密向量测试 | 有，且向量与文档一致（已独立复算）；Stage 4 追加"登录请求体里的密文等于文档向量且明文不出现在请求中"的请求级断言 | 已满足 | — |
| F2 | 心跳回调字段映射测试 | 有（函数级，mock DAO） | 部分满足 | P1 |
| F3 | `rec` 多设备与幂等键测试 | 有（函数级，mock DAO） | 部分满足 | P1 |
| F4 | 请求级回调测试（覆盖 8 类） | **无**；仅覆盖 heartbeat / rec 两类，且均为函数级 smoke test | 缺失 | P1 |
| F5 | 重复事件 / 缺失字段 / 非法字段 / 签名错误测试 | **无** | 缺失 | P1 |
| F6 | 厂商接口 mock 测试（token 刷新、错误映射、参数序列化） | 有（Stage 4）：`httpx.MockTransport` 起 mock 厂商服务，40 个请求级/服务级用例覆盖加密向量、token 缓存与刷新、重试边界、5 个接口的参数位置与包络解包、超时/401/400/404/业务错误/非法响应映射、敏感信息不外泄、本地台账更新与逐台部分失败 | 已满足（Stage 4） | — |
| F7 | 数据库迁移 / 唯一约束 / 新旧库升级测试 | **无** | 缺失 | P1 |
| F8 | 前端回调/组件测试 | **无** | 缺失 | P2 |
| F9 | 测试可在干净环境被收集 | `config/database.py` 改为惰性建引擎；无 MySQL/PG 驱动时仍可收集并跑完 45 个测试 | 已满足（Stage 2） | — |

#### F9 的实测复现

在仅装 `pytest` 的干净 Python 3.12 环境下执行测试收集：

```text
tests/test_device_integration.py:6: in <module>
    from module_device.dao.device_dao import CallbackDao, DeviceDao
...
config/database.py:18: in <module>
    async_engine = create_async_engine(
...
E   ModuleNotFoundError: No module named 'asyncmy'
```

结论：现有测试**必须**先安装全部运行期依赖（含数据库驱动）才能收集，无法作为轻量回归手段。Stage 3 引入请求级测试时需要同时提供可脱离真实数据库的测试装配（例如惰性建引擎或测试专用 `conftest`）。

### 5.8 P0 / P1 / P2 汇总

**P0（必须先在 Stage 2/3 定稿，否则后续返工）**

1. **B6** `upload` 回调类型归一化：文档该类无 `type` 字段，现有实现会误判为 `log`/`rec`。
2. **B11 / D5** 回调幂等键确定性策略：无业务键回调（心跳、upload）`event_id` 为 NULL 无法去重；`rec` 多文件只取首条。
3. **B12** 处理失败时的原始报文留存：当前同事务回滚导致报文丢失，无法审计与重放。

**P1（影响功能正确性与可验收性）**

- B3 心跳 3s 响应时限保护
- B13/B14 回调签名定位与失败审计
- B18 事件时间口径不一致（字符串 naive 与 Unix UTC 混用）
- ~~C2 `sn` query 与 body `sns` 的并存语义~~ → Stage 4 已做成可配置开关，仅剩真实环境确认取值
- ~~C4/D7 配置状态持久化~~ → Stage 4 已落库
- ~~C8 厂商 `msg_id` 本地留存以支持回查~~ → Stage 4 已留存并串起回查
- ~~C9 批量同步部分失败语义~~ → Stage 4 已逐台独立事务 + 逐台结果
- D2/D3 在线状态三态与超时离线兜底（Stage 2 已实现，验收留给 Stage 6）
- D6 录音/转码/ASR 业务模型（Stage 2 已建模，验收留给 Stage 6）
- E4/E5 前端缺失入口（配置同步 / 指令结果查询）
- E6 心跳字段展示完整性
- E10 部分失败反馈（后端已返回逐台结果，前端提示待 Stage 5）
- F4/F5 请求级回调测试；F7 迁移测试（Stage 2 已补，F4/F5 留给 Stage 3 补齐）
- ~~F6 厂商接口 mock 测试~~ → Stage 4 已补齐
- ~~F9 测试无法在干净环境被收集~~ → Stage 2 已修 ORM 侧；Stage 4 另修 `config/env.py` 在 import 期解析 `sys.argv` 导致带参数的 pytest 无法运行的问题

**P2（体验与完备性）**

- A7 业务错误码表
- B15/B16 `session_id` / `topic_name` 留存
- B17 其余回调响应体约定
- E11 危险操作确认
- F8 前端测试

### 5.9 "文档不明确" 清单（不得靠猜字段名补齐）

| 编号 | 不明确项 | 现有处理 | 待确认方式 |
| --- | --- | --- | --- |
| U1 | 鉴权头是否带 `Bearer ` 前缀 | 默认 `Bearer`，可用 `MINGLUE_API_AUTH_SCHEME` 置空 | 真实环境调一次 `status/batch`，无需改代码 |
| U2 | 批量接口 `sn` query 与 body `sns` 是否都需要 | 默认两者都发；`MINGLUE_API_BATCH_SN_QUERY=false` 可只发 body | 真实环境分别试送，无需改代码 |
| U3 | `config_data` 结构 | 未建模，仅原样回传 | 向厂商索取配置字段定义或取一次真实返回 |
| U4 | `nm` 类型（表标 int64，示例为字符串） | 按字符串处理 | 真实心跳核对 |
| U5 | `charged_status` 满电取值（`finished` vs `finish`） | 原样存储，不做枚举校验 | 真实心跳/日志核对 |
| U6 | `upload` 的 `status` 取值含义（示例 6） | 原样存储 | 向厂商索取状态表 |
| U7 | `fc.status` 取值域（示例 `completed`） | 原样存储 | 向厂商索取状态表 |
| U8 | `asr.status` 取值域（示例 100） | 原样存储 | 向厂商索取状态表 |
| U9 | `payload` / `response`（指令日志）结构 | 原样回传 | 向厂商索取 |
| U10 | 除心跳外 7 类回调的成功响应体 | 统一返回 `{"code":0}` | 真实联调观察厂商是否重推 |
| U11 | 业务错误码 `code` 非 0 的取值表 | 仅透传 `message` | 向厂商索取 |

---

## 6. 仍需真实环境确认的事项

1. **鉴权头形态**（U1）：`Authorization: <token>` 还是 `Authorization: Bearer <token>`。
2. **批量接口参数位置**（U2）：`sn` 是否真的作为 query 必填，还是文档冗余；body `sns` 是否足够。
3. **回调接收地址与响应约定**（U10）：除心跳外 7 类回调，厂商对非 2xx / 非 `{"code":0}` 的重推策略未知。
4. **厂商示例 AES 密钥的敏感级别**：Apifox 文档示例中出现 16 字节密钥，当前仓库测试向量引用了同一值。需确认它是公开示例还是真实凭证；若是真实凭证，Stage 6 必须移除并改用非敏感测试向量。
5. **回调签名**：厂商文档未定义签名机制。现有可选 HMAC-SHA256 只能作为本系统加固能力，需在 Stage 3 明确"默认关闭 + 开启后的兼容策略"，不得对外声称是厂商标准。
6. **心跳频率与落库压力**：每台设备约 3 分钟一次心跳，当前每跳一条回调日志且无幂等；需按真实设备规模评估是否需要心跳降采样或独立表。
7. **日志文件下载与解析**：`sys`/`op`/`reclist` 的结构化字段在 `object_key` 指向的日志文件里，`download_url` 有有效期。是否需要下载解析、以及对象存储访问方式，需与厂商确认。
8. **厂商测试环境与设备**：所有控制类接口（开启/停止录音）**不得**对生产设备发起；真实联调必须使用用户明确授权的测试环境与测试设备。

---

## 7. 对后续阶段的输入

| 阶段 | 本阶段给出的输入 |
| --- | --- |
| Stage 2（数据模型/DAO/迁移） | §2、§3 的完整字段字典；§5.5 的 D1–D10 差距；P0 的 B11/D5 幂等键策略、D6 业务模型、D7 配置字段 |
| Stage 3（回调精确适配） | §2 的 8 类回调契约；P0 的 B6/B11/B12；U3–U10 待确认项；B13/B14 签名定位 |
| Stage 4（设备控制对接） | §4 的 5 个接口契约；C2/C4/C8/C9 差距；U1/U2/U9/U11 待确认项。**Stage 4 交付**：§4.6 一致性规则、§4.7 本地接口清单、C2/C4/C8/C9/C10/C11 关闭、F6 关闭 |
| Stage 5（前端页面） | E1–E11 差距；§3 心跳字段字典用于详情展示；**新增输入**：§4.7 的本地接口清单（含 `PUT /device/status`、指令结果查询的 `{remote, remote_error, local}` 结构、批量同步的 `partial`/`results` 逐台结果，用于部分失败提示） |
| Stage 6（全链路验收） | §5.8 的 P0/P1/P2 汇总作为验收清单；§6 全部真实环境事项；**Stage 4 追加**：`config/env.py` 在 pytest 下的 argv 处理、`exceptions/exception.py` 的 message 传递、U1/U2 两种开关取值的真实环境验证 |

### 7.1 本阶段结论

- 首版实现（`agent/codex/gytai-123`）已在框架、设备台账、回调接收、控制接口、页面与权限上形成可用骨架，**未偏离 Dash-FastAPI-Admin 的目录与权限体系**。
- 与厂商契约的**结构性偏差集中在回调侧**：类型归一化、幂等键、失败留存三项为 P0，必须在 Stage 2/3 定稿。
- 厂商文档自身存在多处矛盾与留白（U1–U11），后续阶段**不得凭字段名猜测**，应按 §5.9 逐项确认并回写本文档。
- 本阶段**未做大规模业务代码改造**，仅纳入既有实现、补齐契约与差距文档。

---

## 8. 本阶段静态检查结果

执行环境：macOS，仓库工作区；Python 3.12（`/Library/Frameworks/Python.framework/Versions/3.12/bin/python3`，pytest 8.2.0）。

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 后端新增模块 lint | `ruff check module_device/ tests/` | ✅ `All checks passed!` |
| Python 语法编译 | `python3 -m compileall -q module_device/ tests/` | ✅ 通过 |
| 空白/冲突标记 | `git diff --check` | ✅ 通过 |
| MySQL 与 PG 表结构等价 | 逐表比对 `ml_device` / `ml_callback_log` 列集合 | ✅ 两表列数一致（34/34、11/11），无单侧列 |
| AES 加密向量 | 用 `openssl enc -aes-128-ecb` 独立复算文档向量 | ✅ 得到 `R5V4MqWkJ4kD/zNcqaxPwQ==`，与测试断言一致 |
| 回调类型/幂等键映射 | 桩模块加载 `CallbackService`，按 8 类文档样例逐项喂入 | ⚠️ 复现 B6（`upload` 误判）与 B11（心跳 `event_id=None`、`rec` 只取首条），见 §5.3 |
| 时间解析口径 | 同上，实测字符串与 Unix 秒两条路径 | ⚠️ 复现 B18 口径不一致 |
| 现有测试套件 | `pytest tests/` | ❌ 无法收集：缺少 `asyncmy`，`config/database.py` import 期建引擎（F9） |
| 敏感信息扫描 | 全仓搜索 Apifox 密码、分享口令、`s.apifox.cn` | ✅ 未发现密码或口令进入仓库；文档仅保留分享 ID 用于溯源 |

补充说明：

- 仓库内 `utils/common_util.py:16` 存在上游框架自带的 `SyntaxWarning: invalid escape sequence '\ '`，非本阶段引入，Stage 6 可一并清理。
- 本阶段**未做大规模业务代码改造**，除契约文档与 README 指引外未改动业务逻辑，因此 lint/编译结果与基线一致。

### 8.1 Stage 4 验证结果（GYTAI-124）

| 检查项 | 命令 | 结果 |
| --- | --- | --- |
| 测试套件 | `pytest` | ✅ **86 passed**（Stage 2 基线 45 + Stage 4 新增 41） |
| 测试可用性 | `pytest -q tests/test_minglue_stage4_vendor_api.py` | ✅ 通过；带参数调用 pytest 不再被 `config/env.py` 的 import 期 `argparse` 中断 |
| 后端 lint | `ruff check module_device/ tests/ config/ exceptions/` | ✅ `All checks passed!` |
| Python 语法编译 | `compileall -q module_device/ tests/ config/ exceptions/` | ✅ 通过 |
| 空白/冲突标记 | `git diff --check` | ✅ 通过 |
| mock 厂商覆盖 | `httpx.MockTransport` 起 mock 服务，按 5 个设备接口断言请求 | ✅ 路径/方法/query/body/鉴权头逐项断言 |
| 加密向量 | 登录请求体断言 `password == R5V4MqWkJ4kD/zNcqaxPwQ==` 且明文不出现 | ✅ 通过 |
| token 生命周期 | 缓存命中不重复登录、401 强制刷新只重试一次、连续 401 的报错不含凭据 | ✅ 通过 |
| 错误映射 | 超时 / 连接失败 / 400 / 404 / `code!=0` / 非 JSON / 非对象 / 缺 `entities` / 缺 `msg_id` | ✅ 均有确定行为与可读错误 |
| 敏感信息 | 错误信息脱敏断言 + 控制日志不含 token | ✅ 通过 |
| 本地台账一致性 | 逐台 savepoint 提交、单台失败不影响其他设备、缺项序列号有结论 | ✅ 通过 |
| 敏感信息扫描 | 全仓搜索 Apifox 密码、分享口令、厂商 token | ✅ 未进入仓库 |

补充说明：

- 本阶段环境未安装运行期依赖 `loguru` 等，未做应用启动级 smoke test；`config/get_db.py` 因此无法在测试中导入，指令回查逻辑下沉到服务层以便测试（见 §4.7）。
- `exceptions/exception.py` 的 6 个自定义异常原先不向基类传递 `message`，`str(exc)` 恒为空串，导致审计字段 `error_message` 会丢失原因；本阶段修正为 `super().__init__(message)`。

---

## 9. 阶段交接的遗留说明

### 9.1 Stage 3 未交付

本阶段开始时，Stage 3（GYTAI-125：厂商回调接口精确适配）**没有任何交付物**：
issue 状态为 `in_progress`，无结果评论，仓库中不存在 `agent/codebuddy/gytai-125`
分支，`git worktree list` 也没有该任务的 worktree，`origin` 上同样没有对应分支。

因此本阶段**未沿用** Stage 3 报告的分支/提交，改为从 Stage 2 已交付的
`agent/codebuddy/gytai-128` 的 `dcf8486` 继续开发：

- 契约输入来自 Stage 1 已交付的 `agent/codebuddy/gytai-127` 的 `7a6b127`
  （即本文档 §1–§4），满足本阶段"使用真实参数位置、字段名、鉴权与响应结构"的要求。
- Stage 3 负责的回调侧内容（B6/B11/B12 已在 Stage 2 关闭，B3/B13/B14/B18 仍未完成）
  与本阶段交付的设备管理/厂商控制接口**相互独立**，不构成本阶段的实现阻塞；
  §5.3 的 B3/B13/B14/B18 与 F4/F5 仍为未关闭项，需要在 Stage 3 补做。
- 若 Stage 3 后续从 `dcf8486` 继续，本阶段分支与它的改动集不重叠（本阶段只动
  `module_device` 的设备管理与控制侧、`config/env.py`、`exceptions/exception.py`、
  `.env.dev`/`.env.prod` 的两个开关、`tests/` 与本文档），合并冲突风险低；
  Stage 5/6 需确认两者都在同一分支上。

---

## 附：文档维护约定

1. 任何阶段发现契约偏差，先更新本文档对应小节与 §5 矩阵，再改代码。
2. 每次更新必须在文首修订"文档更新时间"与版本号，并在下表追加记录。
3. 厂商文档更新后，需重新抓取 `doc` / `http-apis` / `data-schemas` 并逐项复核 §2–§4。

| 版本 | 日期 | 更新人 | 变更摘要 |
| --- | --- | --- | --- |
| v1.0 | 2026-09-22 | CodeBuddy（Stage 1） | 首次盘点：4 篇说明 + 6 个接口契约，8 类回调，差距矩阵与 P0/P1/P2 排序 |
| v1.1 | 2026-09-22 | CodeBuddy（Stage 2） | 数据模型/DAO/迁移定稿：关闭 B6/B11/B12、D2/D3/D5/D6/D7、F9；新增 `ml_recording_file`、`ml_device_control_log`；新增可重复执行的双库迁移脚本；补 45 个模型/DAO/迁移测试 |
| v1.2 | 2026-09-22 | CodeBuddy（Stage 4） | 厂商控制接口精确对接：关闭 C2/C4/C8/C9/C10/C11 与 F6；新增 §4.6 远端同步/本地台账一致性规则、§4.7 本地设备管理接口清单；补 40 个 mock 厂商请求级/服务级测试 |
