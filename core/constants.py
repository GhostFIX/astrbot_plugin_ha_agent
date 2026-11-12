"""插件级常量。

只放不可配置的固定值，可配置项全部在 ``_conf_schema.json`` 里。
"""

from __future__ import annotations

PLUGIN_NAME = "astrbot_plugin_ha_agent"
PLUGIN_DISPLAY_NAME = "HA 指令助手"
PLUGIN_VERSION = "1.2.2"

# 默认写入 HA 事件总线的事件类型。
DEFAULT_EVENT_TYPE = "astrbot_command"

# 告警回传监听的默认端口与路径。
DEFAULT_INBOUND_PORT = 6199
INBOUND_PATH = "/ha/notify"

# 唯一的插件视图目录名（views/<VIEW_NAME>/index.html）。
VIEW_NAME = "settings"

# 单条指令与单条告警的长度上限，防止误发超长文本把聊天刷爆。
MAX_COMMAND_CHARS = 500
MAX_NOTIFY_CHARS = 4000

# 网络超时（秒）。ping 用短超时，避免帮助卡片卡住。
HA_REQUEST_TIMEOUT = 10.0
HA_PING_TIMEOUT = 3.0

# MQTT 默认值。主题前缀可在配置里改，两侧主题由前缀派生。
MQTT_DEFAULT_PORT = 1883
MQTT_KEEPALIVE = 60
MQTT_QOS = 1
MQTT_TOPIC_PREFIX = "astrbot/ha"

DATA_DIR_NAME = "plugin_data"
INBOUND_TOKEN_FILE = "inbound_token.txt"
