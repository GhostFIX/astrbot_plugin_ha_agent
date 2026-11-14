# astrbot_plugin_ha_agent

把 AstrBot 当作 **Home Assistant 的纯文本输入输出端口**。不调用任何大模型，
不做自然语言理解，`/ha` 后面写什么，HA 就收到什么。

两个方向、各两种传输方式，可以自由组合：

| 方向 | REST | MQTT |
|---|---|---|
| AstrBot → HA | `POST /api/events/astrbot_command` | 发布 `astrbot/ha/command` |
| HA → AstrBot | `POST /ha/notify`（带令牌） | 订阅 `astrbot/ha/notify` |

```
你: /ha 开电脑
      │
      ├─ REST ──▶ HA 事件总线（trigger: platform: event）
      └─ MQTT ──▶ astrbot/ha/command（trigger: platform: mqtt）
                        │
                   automation 触发 → switch.turn_on
```

反方向：

```
HA automation（温度 > 30°C）
      ├─ rest_command ──▶ AstrBot :6199/ha/notify
      └─ mqtt.publish ──▶ astrbot/ha/notify
                        │
                  指定会话收到一条文字
```

## 特性

- 只有 **1 条指令**，出站/入站传输方式可选
- 命令走 HA **事件/消息**语义，同一条命令重复发送每次都会送达（不受"状态值未变化"影响）
- 告警目标是显式配置项，**留空即完全不接收告警**
- 会话白名单/黑名单，与同作者的 Council 插件判定规则一致
- HTTP 回传端口可自定义；令牌在插件页生成、**只显示一次**；MQTT 为纯出站连接，**Docker 不用映射端口**
- 依赖只有 `aiohttp` + `paho-mqtt`

## 两个入口

| 入口 | 用途 |
|---|---|
| 插件详情页 → **HA 指令助手**（同一页两个 Tab） | **配置**：切换 REST/MQTT 后只显示相关字段，告警目标/黑白名单支持下拉多选 + 手填，保存即热生效；**日志**：本插件日志的表格视图 |
| 插件卡片 → **配置**（AstrBot 原生配置页） | 全部 17 项平铺，备查、应急修改；UMO 字段可用会话选择器点选 |

两边读写同一份配置，改哪边都生效。设置页保存后无需重载插件。

## 日志 Tab

插件详情页 → **HA 指令助手** → **日志**，与配置同页，表格展示：

- 四列：**时间 / 级别 / 来源 / 内容**，表头吸顶，长内容自动换行
- 来源过滤：日志器名、源文件路径或消息里出现插件目录名才收录，不会混进别家的日志
- 内存缓冲最近 **400 条**，重载插件后清空；完整日志仍在 AstrBot 的日志文件里
- 支持级别过滤、3 秒自动刷新（页面不可见或停在配置 Tab 时暂停）、手动刷新、清空
- 悬停右上角计数可看到当前挂在哪些日志器上；如果一直是 0 条，多半是挂载失败，
  先看 AstrBot 日志里的 `日志缓冲挂载失败`

## 选哪种？

| 你的情况 | 建议 |
|---|---|
| AstrBot 与 HA 同网段，能接受多映射一个端口 | **全 REST**（默认，失败有状态码，最好排查） |
| AstrBot 在 Docker 里，不想再开端口 | **全 MQTT**（只需容器能连上 Broker） |
| 想复用已有的 Mosquitto / HA MQTT 集成 | **全 MQTT** |
| 出站想稳、入站想省端口 | `outbound_transport=rest` + `inbound_transport=mqtt` |

## 安装

### 从 GitHub 安装（推荐）

AstrBot WebUI → `插件` → `安装插件` → 填入：

```
https://github.com/GhostFIX/astrbot_plugin_ha_agent
```

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/GhostFIX/astrbot_plugin_ha_agent
```

> **Docker 提示**：只有 `inbound_transport=http` 时才需要把端口映射出来
> （`-p 6199:6199`）。两个方向都用 MQTT 的话，容器不需要任何入站端口。

## 配置项

### 出站

| 配置 | 默认 | 说明 |
|---|---|---|
| `outbound_transport` | `rest` | `rest` 用 HA REST API；`mqtt` 发布到 Broker |
| `ha_url` | 空 | `http://192.168.1.5:8123`，结尾 `/api` 自动补 |
| `ha_token` | 空 | HA「长期访问令牌」 |
| `event_type` | `astrbot_command` | REST 模式下写入 HA 的事件类型 |
| `verify_ssl` | `true` | 自签名证书才关 |
| `reply_on_success` | `true` | 推送成功后是否回复提示；关掉后成功不吭声，**失败始终提示** |

### 入站

| 配置 | 默认 | 说明 |
|---|---|---|
| `inbound_transport` | `http` | `http` / `mqtt` / `off` |
| `inbound_port` | `6199` | **容器内**监听端口，可改 |
| `notify_umos` | `[]` | 告警推送目标会话（**可多选**）；留空 = 不接收告警 |

> 回传令牌不再走配置：在插件页「配置 → 告警回传令牌」点「生成新令牌」，
> 明文只显示一次，之后只显示指纹。

### MQTT

| 配置 | 默认 | 说明 |
|---|---|---|
| `mqtt_host` | 空 | Broker 地址，如 `192.168.1.5`；同网络可用容器名 |
| `mqtt_port` | `1883` | TLS 一般用 `8883` |
| `mqtt_username` | 空 | 不需要认证则留空 |
| `mqtt_password` | 空 | 同上 |
| `mqtt_tls` | `false` | 使用系统受信任证书（不支持自签名） |
| `mqtt_topic_prefix` | `astrbot/ha` | 指令发 `<前缀>/command`，告警收 `<前缀>/notify` |

### 权限

| 配置 | 默认 | 说明 |
|---|---|---|
| `allowed_umos` | `[]` | 指令白名单，空 = 不限制 |
| `blocked_umos` | `[]` | 指令黑名单，优先级高于白名单 |

告警目标怎么填：在目标会话发一条 `/ha check`，状态卡片会显示 SID（即 UMO）；
设置页里既可以下拉选（发过 `/ha` 的会话会自动进候选），也可以手动粘贴，支持多选。

## 快速开始（REST）

1. 填 `ha_url`、`ha_token`，重载插件
2. 群里发 `/ha check`，状态卡片里 `出站：HA 事件 … ✓ 可达`
3. HA → **开发者工具 → 事件 → 监听** `astrbot_command`
4. 群里发 `/ha 开电脑`，页面应立刻出现 payload
5. 确认字段后写自动化

## HA 侧配置（REST）

### 让 HA 推告警给 AstrBot

`configuration.yaml`：

```yaml
rest_command:
  astrbot_notify:
    url: "http://192.168.1.10:6199/ha/notify"
    method: POST
    headers:
      X-Auth-Token: !secret astrbot_ha_token
    payload: >-
      {"message": {{ message | tojson }},
       "title": {{ title | default('') | tojson }}}
    content_type: "application/json"
```

`secrets.yaml`：

```yaml
astrbot_ha_token: "插件页「配置 → 告警回传令牌 → 生成新令牌」拿到的字符串"
```

### 温度超限告警

```yaml
- id: ha_to_astrbot_temp_alert
  alias: 温度过高推送到 AstrBot
  trigger:
    - platform: numeric_state
      entity_id: sensor.living_room_temperature
      above: 30
      for: "00:05:00"
  action:
    - service: rest_command.astrbot_notify
      data:
        message: "🌡️ 客厅 {{ states('sensor.living_room_temperature') }}°C，已超 30°C"
        title: 高温告警
```

### 收到「开电脑」就开电脑

```yaml
- id: astrbot_open_pc
  alias: AstrBot 开电脑
  trigger:
    - platform: event
      event_type: astrbot_command
  condition:
    - "{{ trigger.event.data.command == '开电脑' }}"
  action:
    - service: switch.turn_on
      target:
        entity_id: switch.pc_power
```

## HA 侧配置（MQTT）

前提：HA 已装 **Mosquitto broker** 集成，插件里 `outbound_transport=mqtt`、
`inbound_transport=mqtt`、`mqtt_host` 指向 Broker。

### 收到「开电脑」就开电脑

```yaml
- id: astrbot_open_pc_mqtt
  alias: AstrBot 开电脑（MQTT）
  trigger:
    - platform: mqtt
      topic: astrbot/ha/command
  condition:
    - "{{ trigger.payload_json.command == '开电脑' }}"
  action:
    - service: switch.turn_on
      target:
        entity_id: switch.pc_power
```

> 用 `mqtt` 触发器，不要给命令单独建传感器再套状态触发器——状态相同的消息
> 不会产生状态变化，第二次「开电脑」就会被吞掉。`mqtt` 触发器每条消息都触发。

### 温度超限告警

```yaml
- id: ha_to_astrbot_temp_alert_mqtt
  alias: 温度过高推送到 AstrBot（MQTT）
  trigger:
    - platform: numeric_state
      entity_id: sensor.living_room_temperature
      above: 30
      for: "00:05:00"
  action:
    - service: mqtt.publish
      data:
        topic: astrbot/ha/notify
        qos: 1
        payload: >-
          {"title": "高温告警",
           "message": "🌡️ 客厅 {{ states('sensor.living_room_temperature') }}°C，已超 30°C"}
```

手工测试：

```bash
mosquitto_pub -h 192.168.1.5 -t astrbot/ha/notify -m "hello"
mosquitto_pub -h 192.168.1.5 -t astrbot/ha/command -m '{"command":"开电脑"}'
```

## 指令

| 输入 | 结果 |
|---|---|
| `/ha 开电脑` | 发送 `command` = `开电脑` |
| `/ha 开 电脑` | `command` = `开 电脑`，中间空格保留 |
| `/ha` | 只回用法（不发送） |
| `/ha check` | 状态卡片：SID、出站/入站连通性、告警目标（不含令牌） |
| 同一条命令连发 5 次 | 发 5 次，不去重 |

`/ha`、`/ha check` 都受 `allowed_umos` / `blocked_umos` 约束，名单外一律回
「❌ 无权限」，不会泄露用法。`check` 是保留字，不能作为命令发给 HA。

成功提示可以用 `reply_on_success` 关掉（群安静一些）；但**发送失败、未配置、
无权限这类提示不受开关影响**，永远会回。

回复示例：

```
✅ 已发送 → HA · astrbot_command
「开电脑」
```

```
✅ 已发送 → MQTT · astrbot/ha/command
「开电脑」
```

payload（REST 事件体 / MQTT 消息体相同）：

```json
{
  "command": "开电脑",
  "raw": "/ha 开电脑",
  "sender": "张三",
  "sender_id": "12345678",
  "session": "aiocqhttp:GroupMessage:987654321",
  "platform": "aiocqhttp",
  "timestamp": "2026-11-12T11:52:03+08:00"
}
```

## 告警契约

### HTTP

```
POST http://<astrbot>:6199/ha/notify
X-Auth-Token: <插件页生成的令牌>        # 也接受 Authorization: Bearer ...
Content-Type: application/json

{"message": "客厅 31°C", "title": "高温告警"}
```

| 情况 | 状态码 | 返回 |
|---|---|---|
| 成功 | 200 | `{"ok": true, "delivered": true, "sent": 2, "targets": 2}` |
| 令牌错误/缺失 | 401 | `{"ok": false, "error": "invalid_token"}` |
| 未配置告警目标 | 409 | `{"ok": false, "error": "notify_umo_not_configured"}` |
| 消息为空 | 400 | `{"ok": false, "error": "empty_message"}` |
| UMO 格式不合法 | 400 | `{"ok": false, "error": "invalid_umo", "reason": "..."}` |
| 没有平台实例匹配该 UMO | 502 | `{"ok": false, "error": "platform_not_found"}` |
| 平台适配器发送失败 | 502 | `{"ok": false, "error": "send_failed", "reason": "RuntimeError: ..."}` |

失败响应里的 `reason` 是第一个失败目标的原文原因，方便直接在 HA 的
`rest_command` 日志里定位（MQTT 没有响应，原因写进 AstrBot 日志）。

### MQTT

主题 `<前缀>/notify`，payload 与 HTTP 请求体一致；也接受**纯文本**
（整段当作 message）。QoS 1。没有状态码，投递失败只写 AstrBot 日志：

```
[WARN] astrbot_plugin_ha_agent: MQTT 告警未投递（notify_umo_not_configured）：客厅 31°C
```

### 多行消息

- 正文内部的换行（包括空行）**原样保留**，首尾空行会被去掉
- `\r\n` 与裸 `\r` 自动归一化成 `\n`，避免聊天里出现多余回车
- 在 HA 里拼多行内容时务必用 `tojson`，例如
  `{"message": {{ my_multiline_var | tojson }}}`；把裸换行直接塞进 JSON 字符串
  会导致 `invalid_json`

## 排查

| 现象 | 大概率原因 | 怎么办 |
|---|---|---|
| `❌ 发送失败：令牌无效` | token 错或已撤销 | 重新生成长期访问令牌 |
| `❌ 发送失败：无法连接` | 地址/端口/防火墙 | 浏览器直开 `http://<ha>:8123/api/`，应返回 `API running.` |
| `❌ MQTT 未就绪：未配置 mqtt_host` | 选了 mqtt 但没填 Broker | 填 `mqtt_host` 后重载 |
| `❌ MQTT 未连接 Broker` | Broker 地址/账号/网络不对 | 帮助卡片里 `出站：MQTT … ✗`；用 MQTTX 先连通 |
| `❌ 未安装 paho-mqtt` | 依赖没装上 | `pip install paho-mqtt` 后重载插件 |
| `✅ 已发送` 但 HA 没动 | 事件名/主题不同、自动化没启用、条件写错 | 开发者工具→事件监听，或 MQTTX 订阅对应主题对比内容 |
| HA 报 401 | 两边令牌不一致 | 页面重新生成令牌后同步更新 `secrets.yaml` |
| HA 报 409 | 没配告警目标 | 填 `notify_umos` |
| HA 报 502 | 目标平台掉线或 UMO 写错 | 看响应里的 `reason`；`send_failed` 是平台适配器报错（如微信风控/掉线），`platform_not_found` 才是 UMO 不匹配 |
| 告警日志出现 `send_failed` | 平台适配器自身失败 | 用同一 UMO 在 AstrBot 里手动发一条对比；个人微信常见原因是掉线、需要近期互动、触发风控 |
| 接口成功但群里没消息 | 平台不支持主动消息（如 QQ 官方接口） | 换 OneBot / Telegram 等平台 |
| 插件详情页仍只看到旧版「日志」入口 / 入口重复 | 安装目录里残留 `views/logs`（1.2.x 早期版本） | 删掉该目录后重载插件；版本应为 1.2.2，且 `views/` 下只有 `settings` |
| 帮助卡片显示端口绑定失败 | 端口被占用 | 改 `inbound_port` 并同步改 HA 侧 URL |
| Docker 里端口映射了还是连不上 | 只映射了宿主端口，没映射容器端口 | `-p 6199:6199` 两侧都要对；或改用 MQTT |

## 设计取舍

**为什么命令走事件/消息而不是实体？**
实体表达"当前值"，相同值不产生状态变化；命令表达"发生了一次"。用实体转发
命令时，连发两次「开电脑」，第二次不会触发自动化。HA 的事件总线与 MQTT
触发器都按"发生"设计，重复发送每次都送达。

**为什么 MQTT 不做设备发现、不建实体？**
同上。给命令配一个 sensor，就得靠状态变化触发，立刻掉进同一个坑；要做就得
引入序号或时间戳绕路。本插件的定位是"通道"，不是"设备"。

**为什么不复用 AstrBot 的 `register_web_api`？**
那条路由挂在 WebUI 鉴权链路后面，不同版本行为不一致。独立监听 + 自己的令牌，
行为完全可预测。

**为什么告警目标留空就不推？**
宁可返回 409 让你看见"没配置"，也不要静默丢弃让你以为 HA 没触发。MQTT 没有
状态码，所以失败会写 WARNING 日志。

**为什么不做去重？**
重复发送同一条命令是正常用法（点了没反应再喊一遍），去重会让第二次静默失效。

## 开发

```bash
pip install -r requirements.txt pytest ruff
ruff check . && ruff format .
pytest -q
```

```
main.py            指令、帮助卡片、生命周期、传输选择、设置热重建
core/access.py     会话黑白名单
core/config.py     配置归一化
core/settings.py   设置页的快照脱敏、校验与合并
core/logs.py       日志缓冲（按来源过滤、跨日志器去重）
core/web_api.py    设置页/日志页后端接口（register_web_api）
core/store.py      回传令牌生成与持久化、已知 UMO 历史
core/transport.py  Transport 协议 + RestTransport
core/mqtt.py       MqttBridge（paho 适配）+ MqttTransport
core/inbound.py    告警回传 HTTP 监听
views/settings/    插件页面：配置 Tab + 日志 Tab（index.html + app.js + style.css）
tests/             单测 + 冒烟 + 前后端契约（字段名/接口/DOM id/版本号比对）
```

`core/*` 不依赖 AstrBot，可以脱离框架单独测试；只有 `main.py` 依赖
`astrbot.api.*`。

**测试边界**：REST 方向用真实的本地 HTTP 服务往返；MQTT 方向用假 paho 客户端
覆盖回调转接、主题路由、发布与错误分支，**没有对真实 Broker 做过联调**；
设置页与日志页的 HTML/JS 只做了静态契约校验（`tests/test_view_contract.py` 比对字段名、
单选值、接口路径、版本号、DOM id），**没有在真实浏览器里点过**。

## License

MIT © 2026 deepseek-flash (GhostFIX)
