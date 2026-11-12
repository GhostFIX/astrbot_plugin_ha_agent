# CHANGELOG

## 1.0.0 — 2026-11-12

首个版本。

### 新增

- `/ha <文字>`：把文字原样作为 `command` 发到 HA 事件总线
  （`POST /api/events/<event_type>`），逐个字段附带发送者、会话、平台、时间
- 告警回传监听：`POST /ha/notify`，令牌校验（`X-Auth-Token` / Bearer），
  超长消息截断，失败原因映射为 401 / 409 / 400 / 502
- 告警目标 `notify_umo` 显式配置，留空即完全不推送
- 会话黑白名单，判定规则与 `astrbot_plugin_council` 一致
- 帮助卡片：显示当前会话 UMO、HA 可达性、事件类型、监听状态、告警目标，
  并按管理员身份决定是否显示完整回传令牌
- 传输层抽象为 `Transport` 协议，当前实现 `RestTransport`

### 设计说明

- 命令使用 HA 事件总线而非 MQTT 实体：事件按"发生"语义，重复命令每次都送达
- 不使用 `register_web_api`：独立监听端口，避免随 AstrBot 版本变化的鉴权行为
- 不对指令去重：重复发送是正常用法
