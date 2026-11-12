# CHANGELOG

## 1.2.2 — 2026-11-12

> 1.2.1 未单独发布，改动一并合入此版本。

### 新增

- 告警目标支持多选：`notify_umos`（列表），逐个目标投递，日志与响应区分
  全部成功 / 部分送达 / 全部失败
- 设置页的告警目标、白名单、黑名单改为「下拉选择 + 手动粘贴」组合控件：
  发过 `/ha` 的会话自动进入候选，也能直接粘贴任意 UMO
- 新增 `GET /<插件>/settings/umos`：返回已知会话候选（历史记录 + 配置里已填的）
- 插件页面改为「配置 / 日志」双 Tab（同一个 views 页面，不再拆成两个视图）；
  日志以表格呈现（时间 / 级别 / 来源 / 内容），支持级别过滤、自动刷新（3 秒）、
  手动刷新与清空，内存保留最近 400 条
- `GET /<插件>/logs`、`POST /<插件>/logs/clear`
- `core/logs.py`：按来源路径/日志器名/消息内容过滤日志，同一记录跨日志器去重

### 变更

- `notify_umo`（单值）被 `notify_umos`（列表）取代；旧配置读取时自动并入，
  设置页保存后清理旧键
- HTTP 回传响应新增 `sent` / `targets` 字段，例如
  `{"ok": true, "delivered": true, "sent": 2, "targets": 3}`

## 1.2.0 — 2026-11-12

新增 WebUI 设置页：切换传输方式后只显示相关配置项。

### 新增

- `views/settings/`：Dashboard 插件视图
  - 出站切换 `REST 事件` / `MQTT 发布`，只渲染对应字段
  - 入站切换 `HTTP 端口` / `MQTT 订阅` / `关闭`，只渲染对应字段
  - 主题前缀仅在任一方向使用 MQTT 时显示
  - 「保存并生效」写回插件配置并热重建通道，无需重载插件
  - 「测试连接」分别报出站与入站状态
  - 密钥字段回显为空并标注「已设置，留空不修改」，避免误清空
- `core/settings.py`：设置快照脱敏、提交校验与合并（纯函数）
- `core/web_api.py`：`GET/POST /<插件>/settings`、`POST /<插件>/settings/test`
- 主类新增 `save_settings()` / `check_channels()` / 运行时热重建

### 变更

- `_conf_schema.json` 保持 17 项平铺不变，作为完整选项备查页
- 出站/入站通道的建立与销毁收敛到 `_apply_runtime()` / `_shutdown_runtime()`

## 1.1.0 — 2026-11-12

新增 MQTT 双向通道，出站与入站传输方式各自独立可选。

### 新增

- `outbound_transport`：`rest`（默认）/ `mqtt`
  - REST 仍走 `POST /api/events/<event_type>`
  - MQTT 发布到 `<前缀>/command`，HA 用 `mqtt` 触发器监听
- `inbound_transport`：`http`（默认）/ `mqtt` / `off`，取代 1.0 的 `inbound_enabled`
  - MQTT 订阅 `<前缀>/notify`，payload 与 HTTP 接口一致，也接受纯文本
- MQTT 配置：`mqtt_host`、`mqtt_port`、`mqtt_username`、`mqtt_password`、
  `mqtt_tls`、`mqtt_topic_prefix`（派生 `/command` 与 `/notify`）
- 出站与入站共用一条 MQTT 连接；断线由 paho 自动重连
- 传输层统一为 `Transport.send()`，业务代码不区分 REST / MQTT

### 变更

- 回复文案带上传输方式：`✅ 已发送 → HA · astrbot_command` /
  `✅ 已发送 → MQTT · astrbot/ha/command`
- 帮助卡片分别显示出站方式与入站方式的状态
- 依赖新增 `paho-mqtt>=1.6`

### 已知限制

- MQTT 入站没有 HTTP 状态码，投递失败只写日志（HTTP 方式会返回 409 / 502）
- MQTT 的 TLS 使用系统受信任证书，暂不支持自签名证书

## 1.0.0 — 2026-11-12

首个版本。

### 新增

- `/ha <文字>`：把文字原样作为 `command` 发到 HA 事件总线
  （`POST /api/events/<event_type>`），逐个字段附带发送者、会话、平台、时间
- 告警回传监听：`POST /ha/notify`，令牌校验（`X-Auth-Token` / Bearer），
  超长消息截断，失败原因映射为 401 / 409 / 400 / 502
- 告警目标 `notify_umo` 显式配置，留空即完全不推送
- 会话黑白名单，判定规则与 `astrbot_plugin_council` 一致
- 帮助卡片：显示当前会话 UMO、通道状态、告警目标，并按管理员身份决定
  是否显示完整回传令牌

### 设计说明

- 命令使用 HA 事件总线而非状态实体：事件按"发生"语义，重复命令每次都送达
- 不使用 `register_web_api`：独立监听端口，避免随 AstrBot 版本变化的鉴权行为
- 不对指令去重：重复发送是正常用法
