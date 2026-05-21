#!/bin/bash
# hermes-qq-bot 一键安装脚本
# 用法: bash install.sh
# 要求: Debian/Ubuntu, 已安装 Hermes Agent

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   hermes-qq-bot 一键部署脚本               ║${NC}"
echo -e "${GREEN}║   把真人 QQ 接入 Hermes Agent              ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""

# 1. 检查依赖
echo -e "${YELLOW}[1/7] 检查依赖...${NC}"
command -v hermes >/dev/null 2>&1 || { echo -e "${RED}❌ Hermes Agent 未安装。请先访问 https://hermes-agent.nousresearch.com${NC}"; exit 1; }
command -v xvfb-run >/dev/null 2>&1 || { echo "安装 xvfb..."; sudo apt-get update && sudo apt-get install -y xvfb; }
echo -e "${GREEN}  ✓ 依赖检查完成${NC}"

# 2. 收集配置（支持环境变量预设，方便 agent/无人值守部署）
echo ""
echo -e "${YELLOW}[2/7] 配置 QQ 机器人...${NC}"

# 如果环境变量没设置，再走交互式询问
if [ -z "$BOT_QQ" ]; then read -p "  机器人 QQ 号 (用来登录的小号): " BOT_QQ; fi
if [ -z "$OWNER_QQ" ]; then read -p "  你的 QQ 号 (主人，将拥有完整权限): " OWNER_QQ; fi
if [ -z "$HOME_GROUP" ] && [ "${HOME_GROUP+set}" != "set" ]; then
    read -p "  默认接收消息的 QQ 群号 (留空表示不进群): " HOME_GROUP
fi
if [ -z "$ACCESS_TOKEN" ]; then
    # agent 模式：自动生成随机 token
    if [ -n "$NONINTERACTIVE" ]; then
        ACCESS_TOKEN=$(openssl rand -hex 16 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)
        echo "  自动生成 token: $ACCESS_TOKEN"
    else
        read -p "  OneBot Access Token (随便编一串，越长越安全): " ACCESS_TOKEN
    fi
fi
if [ -z "$WHITELIST_USERS" ] && [ "${WHITELIST_USERS+set}" != "set" ]; then
    if [ -n "$NONINTERACTIVE" ]; then
        WHITELIST_USERS="$OWNER_QQ"
    else
        read -p "  允许使用工具的用户白名单 (逗号分隔，留空仅主人): " WHITELIST_USERS
    fi
fi
if [ -z "$WHITELIST_GROUPS" ] && [ "${WHITELIST_GROUPS+set}" != "set" ]; then
    if [ -z "$NONINTERACTIVE" ]; then
        read -p "  允许使用工具的群白名单 (逗号分隔，留空表示无): " WHITELIST_GROUPS
    fi
fi

WHITELIST_USERS="${WHITELIST_USERS:-$OWNER_QQ}"

# 必填校验
if [ -z "$BOT_QQ" ] || [ -z "$OWNER_QQ" ]; then
    echo -e "${RED}❌ BOT_QQ 和 OWNER_QQ 必填${NC}"
    echo "  用法: BOT_QQ=12345 OWNER_QQ=67890 NONINTERACTIVE=1 bash install.sh"
    exit 1
fi

# 3. 安装 NapCatQQ
echo ""
echo -e "${YELLOW}[3/7] 安装 NapCatQQ...${NC}"
if [ ! -d "/root/Napcat" ]; then
    cd /tmp
    curl -o napcat.sh https://nclatest.znin.net/NapNeko/NapCat-Installer/main/script/install.sh
    sudo bash napcat.sh --docker n --cli y --force
else
    echo -e "${GREEN}  ✓ NapCat 已安装，跳过${NC}"
fi

# 4. 写入 OneBot 配置
echo ""
echo -e "${YELLOW}[4/7] 写入 OneBot v11 配置...${NC}"
CFG_DIR="/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config"
sudo mkdir -p "$CFG_DIR"
sudo tee "${CFG_DIR}/onebot11_${BOT_QQ}.json" >/dev/null <<EOF
{
    "network": {
        "httpServers": [
            {
                "name": "hermes-http",
                "enable": true,
                "port": 3000,
                "host": "127.0.0.1",
                "enableCors": true,
                "enableWebsocket": true,
                "messagePostFormat": "array",
                "token": "${ACCESS_TOKEN}",
                "debug": false
            }
        ],
        "websocketServers": [
            {
                "name": "hermes-ws",
                "enable": true,
                "host": "127.0.0.1",
                "port": 3001,
                "messagePostFormat": "array",
                "reportSelfMessage": false,
                "token": "${ACCESS_TOKEN}",
                "enableForcePushEvent": true,
                "debug": false,
                "heartInterval": 30000
            }
        ],
        "httpClients": [],
        "websocketClients": [],
        "plugins": []
    },
    "musicSignUrl": "",
    "enableLocalFile2Url": false,
    "parseMultMsg": false
}
EOF
echo -e "${GREEN}  ✓ 配置写入: ${CFG_DIR}/onebot11_${BOT_QQ}.json${NC}"

# 5. 安装 systemd service
echo ""
echo -e "${YELLOW}[5/7] 创建 systemd service...${NC}"
sudo tee /etc/systemd/system/napcat.service >/dev/null <<EOF
[Unit]
Description=NapCatQQ for hermes-qq-bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
Environment="DISPLAY=:99"
ExecStart=/usr/bin/xvfb-run -a /root/Napcat/opt/QQ/qq --no-sandbox -q ${BOT_QQ}
Restart=on-failure
RestartSec=5
KillMode=process
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable napcat
echo -e "${GREEN}  ✓ napcat.service 已注册${NC}"

# 6. 安装 Hermes 插件
echo ""
echo -e "${YELLOW}[6/7] 安装 Hermes napcat 插件...${NC}"
PLUGIN_DIR="${HOME}/.hermes/plugins/platforms/napcat"
mkdir -p "$PLUGIN_DIR"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "${SCRIPT_DIR}/plugin/adapter.py" "$PLUGIN_DIR/"
cp "${SCRIPT_DIR}/plugin/plugin.yaml" "$PLUGIN_DIR/"
cp "${SCRIPT_DIR}/plugin/__init__.py" "$PLUGIN_DIR/"

hermes plugins enable platforms/napcat || true
echo -e "${GREEN}  ✓ 插件已安装并启用${NC}"

# 7. 写入环境变量
echo ""
echo -e "${YELLOW}[7/7] 写入环境变量到 ~/.hermes/.env...${NC}"
ENV_FILE="${HOME}/.hermes/.env"
{
    echo ""
    echo "# === hermes-qq-bot (NapCatQQ) ==="
    echo "NAPCAT_WS_URL=ws://127.0.0.1:3001"
    echo "NAPCAT_HTTP_URL=http://127.0.0.1:3000"
    echo "NAPCAT_ACCESS_TOKEN=${ACCESS_TOKEN}"
    echo "NAPCAT_SELF_ID=${BOT_QQ}"
    echo "NAPCAT_ALLOWED_USERS=${WHITELIST_USERS}"
    echo "NAPCAT_ALLOWED_GROUPS=${WHITELIST_GROUPS}"
    echo "NAPCAT_HOME_USER=${OWNER_QQ}"
    [ -n "$HOME_GROUP" ] && echo "NAPCAT_HOME_GROUP=${HOME_GROUP}"
    echo "NAPCAT_ALLOW_ALL_USERS=false"
} >> "$ENV_FILE"
echo -e "${GREEN}  ✓ 环境变量写入: $ENV_FILE${NC}"

# 完成
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          🎉 安装完成！                      ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════╝${NC}"
echo ""
echo -e "下一步："
echo -e "  1. 启动 NapCat:    ${YELLOW}sudo systemctl start napcat${NC}"
echo -e "  2. 扫码登录 QQ ${BOT_QQ}:"
echo -e "     ${YELLOW}sudo journalctl -u napcat -f${NC}  (二维码会在日志里显示)"
echo -e "     或访问 WebUI: ${YELLOW}http://你的服务器IP:6099${NC}"
echo -e "  3. 重启 Hermes Gateway: ${YELLOW}sudo systemctl restart hermes-gateway${NC}"
echo -e "  4. 用主人 QQ 给机器人发条消息测试"
echo ""
echo -e "${YELLOW}⚠️  注意：${NC}"
echo -e "  - NapCat 必须独立运行，绝对不能跟 hermes-gateway 共 cgroup"
echo -e "  - 主人 QQ: ${OWNER_QQ}  机器人 QQ: ${BOT_QQ}"
echo -e "  - 工具白名单: ${WHITELIST_USERS}"
echo ""
