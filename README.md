# astrbot_plugin_ha_agent

把 AstrBot 当作 **Home Assistant 的纯文本输入输出端口**。不调用任何大模型，
不做自然语言理解，`/ha` 后面写什么，HA 就收到什么。

```
你: /ha 开电脑
      │
      ▼
   AstrBot ──POST /api/events/astrbot_command──▶ HA 事件总线
                                                  │
                                    automation: trigger / condition
                                                  ▼
                                             switch.turn_on
```

反方向：

```
HA automation（温度 > 30°C）──rest_command──▶ AstrBot :6199/ha/notify
                                                  │
                                                  ▼
                                          指定会话收到一条文字
```

## 特性

- 只有 **1 条指令**、**10 个配置项**，无 LLM、无实体解析
- 命令走 HA **事件总线**，同一条命令重复发送每次都会送达（不受"状态值未变化"影响）
- 告警目标是显式配置项，**留空即完全不接收告警**
- 会话白名单/黑名单，与同作者的 Council 插件判定规则一致
- 告警回传端口可自定义，令牌自动生成，支持请求头鉴权
- 依赖只有 `aiohttp`（AstrBot 本身就带）

## 安装

### 从 GitHub 安装（推荐）

AstrBot WebUI → `插件` → `安装插件` → 填入仓库地址：

```
https://github.com/GhostFIX/astrbot_plugin_ha_agent
```

### 手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/GhostFIX/astrbot_plugin_ha_agent
```

装好后重载插件，在插件配置里填 `ha_url` 与 `ha_token`。

> Docker 部署如果要用告警回传，需要把监听端口映射出来，例如
> `-p 6199:6199`。只用 AstrBot → HA 方向则不需要开端口。

## 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `ha_url` | 空 | `http://192.168.1.5:8123`，结尾 `/api` 自动补 |
| `ha_token` | 空 | HA「长期访问令牌」 |
| `event_type` | `astrbot_command` | 写入 HA 的事件类型 |
| `verify_ssl` | `true` | 自签名证书才关 |
| `inbound_enabled` | `true` | 是否监听告警回传 |
| `inbound_port` | `6199` | 告警回传端口，可改 |
| `inbound_token` | 空 | 留空自动生成，存 `data/plugin_data/astrbot_plugin_ha_agent/inbound_token.txt` |
| `notify_umo` | 空 | 告警推送目标会话；**留空 = 不接收告警** |
| `allowed_umos` | `[]` | 指令白名单，空 = 不限制 |
| `blocked_umos` | `[]` | 指令黑名单，优先级高于白名单 |

`ha_token` 获取：HA 左下角个人资料 → 安全 → 长期访问令牌 → 创建令牌。

`notify_umo` 获取：在目标会话里发一条 `/ha`，帮助卡片会显示当前会话 UMO，
复制粘贴即可；也可以直接用 WebUI 的会话选择器点选。

## 快速开始

1. 填好 `ha_url`、`ha_token`，重载插件
2. 在群里发 `/ha`，看到帮助卡片且 HA 地址后面是 `✓ 可达`
3. 在 HA 的 **开发者工具 → 事件 → 监听** 里填 `astrbot_command`，开始监听
4. 在群里发 `/ha 开电脑`，事件页面上应立刻出现一条 payload
5. 确认字段后，去写自动化（见下）

## HA 侧配置

### 1. 让 HA 能推告警给 AstrBot

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
astrbot_ha_token: "把帮助卡片里（或 inbound_token.txt 里）的令牌粘贴到这里"
```

### 2. 温度超限告警

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

### 3. 收到「开电脑」就开电脑

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

一个自动化分发多条命令：

```yaml
- id: astrbot_command_router
  alias: AstrBot 指令路由
  trigger:
    - platform: event
      event_type: astrbot_command
  action:
    - variables:
        cmd: "{{ trigger.event.data.command }}"
    - choose:
        - conditions: "{{ cmd == '开电脑' }}"
          sequence:
            - service: switch.turn_on
              target: { entity_id: switch.pc_power }
        - conditions: "{{ cmd == '睡觉' }}"
          sequence:
            - service: light.turn_off
              target: { entity_id: light.all }
        - conditions: "{{ cmd.startswith('亮度') }}"
          sequence:
            - service: light.turn_on
              target: { entity_id: light.living_room }
              data:
                brightness_pct: "{{ cmd[2:] | int(70) }}"
```

## 指令

| 输入 | 结果 |
|---|---|
| `/ha 开电脑` | 向 HA 发事件，`command` = `开电脑` |
| `/ha 开 电脑` | `command` = `开 电脑`，中间空格保留 |
| `/ha` | 显示帮助卡片（不发送事件） |
| 同一条命令连发 5 次 | 发 5 次事件，不去重 |

事件 payload：

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

## 告警回传接口

```
POST http://<astrbot>:6199/ha/notify
X-Auth-Token: <inbound_token>        # 也接受 Authorization: Bearer ...
Content-Type: application/json

{"message": "客厅 31°C", "title": "高温告警"}
```

| 情况 | 状态码 | 返回 |
|---|---|---|
| 成功 | 200 | `{"ok": true, "delivered": true}` |
| 令牌错误/缺失 | 401 | `{"ok": false, "error": "invalid_token"}` |
| 未配置告警目标 | 409 | `{"ok": false, "error": "notify_umo_not_configured"}` |
| 消息为空 | 400 | `{"ok": false, "error": "empty_message"}` |
| 目标平台不在线 | 502 | `{"ok": false, "error": "platform_not_found"}` |

## 排查

| 现象 | 大概率原因 | 怎么办 |
|---|---|---|
| `❌ 发送失败：令牌无效` | token 错或已撤销 | 重新生成长期访问令牌 |
| `❌ 发送失败：无法连接` | 地址/端口/防火墙 | 浏览器直开 `http://<ha>:8123/api/`，应返回 `API running.` |
| `✅ 已发送` 但 HA 没动 | 事件名不同、自动化未启用、条件写错 | 开发者工具→事件→监听 `astrbot_command` 对比 payload |
| HA 报 401 | 两边令牌不一致 | 核对 `inbound_token` 与 `secrets.yaml` |
| HA 报 409 | 没配告警目标 | 填 `notify_umo` |
| HA 报 502 | 目标平台掉线或 UMO 写错 | 发 `/ha` 看帮助卡片里的 UMO |
| 接口返回成功但群里没消息 | 平台不支持主动消息（如 QQ 官方接口） | 换 OneBot / Telegram 等平台 |
| 帮助卡片显示端口绑定失败 | 端口被占用 | 改 `inbound_port` 并同步改 HA 侧 URL |

## 设计取舍

**为什么命令走事件总线而不是 MQTT 实体？**
实体表达的是"当前值"，相同值不会产生状态变化；命令表达的是"发生了一次"。
用实体转发命令时，连发两次「开电脑」，第二次不会触发自动化。HA 的事件总线
按"发生"设计，重复发送每次都送达。唯一的例外是跨网段部署——那种情况更适合
MQTT，本插件把传输层抽成了接口，后续可以加 `MqttTransport` 而不动业务代码。

**为什么不用 AstrBot 的 `register_web_api`？**
那条路由挂在 WebUI 鉴权链路后面，不同版本行为不一致。独立监听 + 自己的令牌，
行为完全可预测，HA 侧只需要一个 URL。

**为什么告警目标留空就不推？**
宁可返回 409 让你看见"没配置"，也不要静默丢弃让你以为 HA 没触发。

**为什么不做去重？**
重复发送同一条命令是正常用法（点了没反应再喊一遍），去重会让第二次静默失效。

## 开发

```bash
pip install -r requirements.txt pytest ruff
ruff check . && ruff format .
pytest -q
```

目录：

```
main.py            指令、帮助卡片、生命周期
core/access.py     会话黑白名单
core/config.py     配置归一化
core/store.py      回传令牌生成与持久化
core/transport.py  HA REST 传输层（Transport 协议 + RestTransport）
core/inbound.py    告警回传 HTTP 监听
tests/             单测 + 冒烟（真实 HTTP 往返，不打桩业务逻辑）
```

`core/*` 不依赖 AstrBot，可以脱离框架单独测试；只有 `main.py` 依赖
`astrbot.api.*`。

## License

MIT © 2026 deepseek-flash (GhostFIX)
