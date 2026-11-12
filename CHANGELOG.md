# CHANGELOG

## 1.2.6 — 2026-11-12

### 新增

- `reply_on_success` 开关（默认 `true`）：关闭后指令推送成功不再回复，
  群聊更安静；**失败提示不受影响**，未就绪、无权限、发送失败仍然照常回复

## 1.2.5 — 2026-11-12

### 修复

- 告警正文的 `\r\n` / `\r` 统一归一化为 `\n`，多行消息不再带多余回车；
  HTTP 与 MQTT 两条入站路径一致（`normalize_newlines`，`core/text.py`）
- 正文内部换行（含空行）保持原样，仅去掉首尾空白

## 1.2.4 — 2026-11-12

### 变更

- `/ha`（无参数）现在**只回用法**，且与发送指令一样受 `allowed_umos` /
  `blocked_umos` 约束；名单外统一回「❌ 无权限」，不再泄露用法
- 新增 `/ha check`：会话 ID（SID）、出站/入站连通性、告警目标；同样受名单约束，
  **不展示令牌**。`check` 为保留字，不能再作为命令发给 HA
- 帮助卡片彻底移除令牌：原「管理员可见完整令牌」的设计取消

### 安全

- 回传令牌改为**只在插件页生成、只显示一次**：插件页新增「生成新令牌」按钮，
  明文仅返回给该次请求，之后只显示指纹（`abcd…wxyz`）
- `inbound_token` 从 `_conf_schema.json` 移除，令牌只存
  `data/plugin_data/astrbot_plugin_ha_agent/inbound_token.txt`
- 升级时自动把配置里的旧令牌迁移到上述文件，保存设置时清理旧键
- 新增 `POST /<插件>/settings/token`

## 1.2.3 — 2026-11-12

### 修复

- 设置页「白名单 / 黑名单 / 告警目标」在超长 UMO 下把双列网格撑破、右侧按钮
  溢出卡片的问题：
  - `.grid2` 改用 `repeat(2, minmax(0, 1fr))`，列宽不再被 `min-content` 顶住
  - 网格子项与 chip 容器补 `min-width: 0`
  - chip 文本单独一层 `.chip-text`，超出用省略号，完整 UMO 保留在 `title` 提示里
- 版本号升到 1.2.3，方便确认安装的是新构建

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
- `.astrbot-plugin/i18n/`：视图入口显示为「HA 指令助手」，不再显示目录名 `settings`
- 启动时检测 `views/` 下多余的子目录（早期版本残留的 `logs`），并在日志里提醒删除

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
