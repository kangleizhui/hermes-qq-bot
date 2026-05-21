# AGENTS.md — 给 AI Agent 的部署指令

> 你（AI agent）正在帮用户部署 hermes-qq-bot。这份文件就是给你看的，按步骤照做即可。
> 用户给你这个仓库链接的目的是：让你帮他/她把 Hermes Agent 接入到一个真人 QQ 号上。

---

## 🎯 任务目标

把这台服务器变成一个 24 小时在线的 QQ 机器人：
- 主人发消息 → Hermes Agent 处理 → 机器人 QQ 自动回复
- 主人也能拿到完整工具权限（终端、文件、浏览器…）

---

## ✅ 前置检查（先问用户这 3 个问题）

在开始之前，**必须先问用户**这 3 个信息（缺一不可）：

1. **机器人 QQ 号**（变量名 `BOT_QQ`）—— 用来登录的小号
   - 提醒用户：**别用主号！** 容易被腾讯封。建议用刚申请的小号。
2. **主人 QQ 号**（变量名 `OWNER_QQ`）—— 主人自己的 QQ，用来指挥机器人
3. **机器人需要进的群号**（变量名 `HOME_GROUP`，可选）—— 没有就让用户回车跳过

可选信息（用户没说就走默认）：
- `WHITELIST_USERS`：能调用工具的用户白名单（默认只主人）
- `WHITELIST_GROUPS`：能调用工具的群白名单（默认无）

---

## 🔍 环境前提

部署前确认这些条件，**任何一项不满足都要先解决**：

```bash
# 1. 系统必须是 Debian/Ubuntu
cat /etc/os-release | head -3

# 2. Hermes Agent 必须已安装并配好 AI
which hermes && hermes --version
# 如果没装：curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
# 配置 AI：hermes setup（让用户自己跑，需要交互输入 API key）

# 3. 必须有 sudo 权限
sudo -n true 2>&1 || echo "需要 sudo 密码"
```

---

## 🚀 部署执行步骤

### Step 1: 克隆仓库

```bash
cd /root
git clone https://github.com/kangleizhui/hermes-qq-bot.git
cd hermes-qq-bot
```

### Step 2: 非交互式一键部署

**把用户给你的参数填进去，然后跑**：

```bash
cd /root/hermes-qq-bot
BOT_QQ="<机器人QQ号>" \
OWNER_QQ="<主人QQ号>" \
HOME_GROUP="<群号或留空>" \
NONINTERACTIVE=1 \
bash install.sh
```

> `NONINTERACTIVE=1` 会让脚本：自动生成 access_token、白名单默认为 OWNER_QQ、群白名单留空。
> 如果用户指定了白名单，加上 `WHITELIST_USERS="..." WHITELIST_GROUPS="..."` 即可。

### Step 3: 启动 NapCat 并提示用户扫码

```bash
sudo systemctl start napcat
sleep 5
sudo journalctl -u napcat -n 100 --no-pager
```

⚠️ **关键点**：日志里会出现一个 **二维码 ASCII 艺术**。你必须：
1. 把这个二维码完整复制给用户，告诉用户**用机器人小号扫码登录**
2. 或者告诉用户访问 NapCat WebUI 扫码：`http://<服务器IP>:6099`（token 在 `/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json`）
3. 等待用户回复"扫好了"再继续下一步

### Step 4: 验证登录成功

```bash
sudo journalctl -u napcat -n 50 --no-pager | grep -iE "登录|login|online"
# 看到类似 "QQ XXX 登录成功" 就 OK
```

### Step 5: 重启 Hermes 让它认识新的 QQ 平台

```bash
sudo systemctl restart hermes-gateway
sleep 5
sudo systemctl status hermes-gateway --no-pager | head -10
```

### Step 6: 验证连通性

```bash
# 看 napcat adapter 是否成功连上
sudo journalctl -u hermes-gateway -n 100 --no-pager | grep -iE "napcat|onebot"
# 期望看到：napcat 已连接 / WebSocket connected / "active=true"
```

### Step 7: 让用户实测

告诉用户：**用主号给机器人小号发一条消息**（比如"你好"），如果秒回就成功了。

如果用户说没回复，按 [troubleshooting](#-排错指南) 排查。

---

## ⚠️ 重要踩坑（必须避免）

### 1. NapCat 必须独立 systemd service

绝对**不要**用 `nohup`、`screen`、`hermes terminal background=true` 来跑 NapCat。`install.sh` 已经创建独立的 `napcat.service`，按它来就行。

**原因**：systemd 重启 `hermes-gateway` 时会 SIGKILL 整个 cgroup，把同 cgroup 的 NapCat 一起干掉。

### 2. 群消息发不出去？检查 chat_id 路由

adapter 用 `_chat_kind_cache` 区分私聊和群聊。这个缓存只在收到入站消息时填充。如果你想用 cronjob 主动 push 到群，需要在 `.env` 里配置 `NAPCAT_HOME_GROUP`。

### 3. 端口冲突

`install.sh` 用了这些端口（127.0.0.1 only）：
- 3000：NapCat HTTP API
- 3001：NapCat WebSocket
- 6099：NapCat WebUI

跑之前先检查：
```bash
ss -tlnp | grep -E ":(3000|3001|6099)\b"
```
如果占用了，要么停掉占用进程，要么改 install.sh 里的端口。

### 4. xvfb 必须装

NapCat 是带 GUI 的 Electron 应用，没图形界面跑不起来。`install.sh` 会自动装 `xvfb`，但有时 apt 源问题会失败，注意看输出。

---

## 🐛 排错指南

| 症状 | 原因 | 解决 |
|------|------|------|
| 扫码后"账号异常" | 小号太新被风控 | 让用户先在手机上养号 3-7 天 |
| NapCat 起不来 | xvfb 没装 / 端口冲突 | `apt install xvfb` / 检查端口 |
| 机器人不回消息 | gateway 没认到 napcat | `systemctl restart hermes-gateway` |
| 群里 @ 没反应 | 群没在白名单 | 编辑 `~/.hermes/.env` 加 `NAPCAT_ALLOWED_GROUPS=` |
| 重启 hermes 后 NapCat 挂了 | 服务依赖搞错了 | 检查 `napcat.service` 是不是独立的 unit |

排错时常用命令：
```bash
sudo systemctl status napcat hermes-gateway --no-pager
sudo journalctl -u napcat -n 100 --no-pager
sudo journalctl -u hermes-gateway -n 100 --no-pager | grep -iE "error|napcat"
cat ~/.hermes/.env | grep NAPCAT_
```

---

## 📋 关键文件位置（备查）

| 路径 | 用途 |
|------|------|
| `~/.hermes/plugins/platforms/napcat/adapter.py` | Hermes 插件主代码 |
| `~/.hermes/.env` | NAPCAT_* 环境变量 |
| `/etc/systemd/system/napcat.service` | NapCat 启动单元 |
| `/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/onebot11_<QQ>.json` | OneBot 配置 |
| `/root/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/webui.json` | WebUI token |

---

## ✅ 完成确认清单

部署完成后必须确认：

- [ ] `sudo systemctl is-active napcat` → `active`
- [ ] `sudo systemctl is-active hermes-gateway` → `active`
- [ ] `~/.hermes/.env` 里 9 个 `NAPCAT_*` 变量都在
- [ ] `~/.hermes/config.yaml` 的 `enabled_plugins` 包含 `platforms/napcat`
- [ ] 用户用主号发消息能收到回复

全部 ✅ 就大功告成。把项目主页地址 + 用户主号能直接发的测试消息丢给他：

> "现在你用主号发 `你好` 给小号 `<BOT_QQ>` 试试 🎉"

---

## 🤝 用户后续可能问的事

- "怎么让陌生人也能聊天但不让他用工具？" → 编辑 `.env` 设 `NAPCAT_ALLOW_ALL_USERS=true`，工具白名单逻辑会在 v0.2 完善
- "怎么让机器人定时发消息？" → 用 Hermes 的 cronjob 功能，target 用 `napcat:<QQ号>` 或 `napcat:<群号>`
- "怎么换 AI 模型？" → `hermes models` 切换
- "可以接多个 QQ 吗？" → 当前版本一个实例一个 QQ，多 QQ 需要改 NapCat 配置（v0.3 规划）
