# CHANGELOG

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
