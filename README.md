# hermes-qq-bot 🤖

> 让 **Hermes Agent** 接管你的真人 QQ —— 一行命令部署，扫码登录就能用。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Hermes Agent](https://img.shields.io/badge/Hermes-Agent-blue)](https://hermes-agent.nousresearch.com)
[![NapCatQQ](https://img.shields.io/badge/NapCat-OneBot%20v11-green)](https://github.com/NapNeko/NapCatQQ)

> 🌟 **新手朋友请直接看这里 →** [**📖 30分钟保姆级部署教程**](docs/QUICKSTART.md)

---

## 这是什么？

把你常用的 QQ 号（或者一个新申请的小号）变成一个 **拥有真正 AI 大脑** 的机器人 —— 它能：

- 📩 在私聊和群里和你正常聊天
- 🔧 帮你查邮件、刷 GitHub、操作 Docker、写代码、跑脚本……（白名单用户专属）
- 🎨 发图片、语音、文件
- 🏠 通过 QQ 主动 push 通知（cronjob、监控告警等）

底层用 [NapCatQQ](https://github.com/NapNeko/NapCatQQ) 做 OneBot v11 协议，[Hermes Agent](https://hermes-agent.nousresearch.com) 提供智能体大脑。

> ⚠️ **免责声明**：QQ 不是官方机器人 API，使用风险自负。建议用小号，不要拿你天天聊女朋友的主号。

---

## 一键部署

```bash
git clone https://github.com/kangleizhui/hermes-qq-bot.git
cd hermes-qq-bot
bash install.sh
```

脚本会问你：
- 机器人 QQ 号
- 你（主人）的 QQ 号
- 默认接收消息的群（可选）
- OneBot Access Token（随便编一串）
- 工具白名单（默认仅主人）

装完按提示扫码登录即可。

---

## 权限模型

**白名单设计**：默认只有白名单里的用户/群能让机器人调用工具（终端、文件、浏览器…）。陌生人只能纯聊天，**不会**给他们执行命令的权限。

| 谁                                | 能聊天 | 能用工具 |
| --------------------------------- | :----: | :------: |
| 主人 (`NAPCAT_HOME_USER`)         |   ✅   |    ✅    |
| 白名单 (`NAPCAT_ALLOWED_USERS`)   |   ✅   |    ✅    |
| 白名单群 (`NAPCAT_ALLOWED_GROUPS`)|   ✅   |    ✅    |
| 其他人                            |   ❌   |    ❌    |

> 想让陌生人也能纯聊天？设置 `NAPCAT_ALLOW_ALL_USERS=true`（只聊不给权限的逻辑会在 v0.2 上线）。

---

## 项目结构

```
hermes-qq-bot/
├── install.sh                    # 一键安装脚本
├── plugin/                       # Hermes 插件（OneBot v11 适配器）
│   ├── adapter.py                # 核心适配器（965 行）
│   ├── plugin.yaml               # 插件元数据
│   └── __init__.py
├── config/
│   └── onebot11.template.json    # OneBot 配置模板
├── systemd/
│   └── napcat.service            # NapCat systemd 单元
└── docs/
    └── PITFALLS.md               # 部署踩坑笔记
```

---

## 核心踩坑（别学我）

### 1️⃣ NapCat 必须用独立 systemd service 跑

❌ **错误**：把 NapCat 当成 hermes-gateway 的子进程（`screen`、`nohup`、Hermes 内 `terminal background=true`）

✅ **正确**：注册独立 `napcat.service`

**原因**：systemd 重启 hermes-gateway 时会 SIGKILL 整个 cgroup，把 NapCat 一起干掉。

### 2️⃣ 群消息路由要缓存 chat_kind

OneBot 群号和私聊 QQ 号都是纯数字，单看 ID 分不清。adapter 用 `_chat_kind_cache` 记录每个 chat_id 是 `"user"` 还是 `"group"`，发消息时正确选择 `send_private_msg` / `send_group_msg`。

### 3️⃣ Hermes 插件路径是两层

放 `~/.hermes/plugins/platforms/napcat/`，启用时 `hermes plugins enable platforms/napcat`。

---

## 环境变量速查

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NAPCAT_WS_URL` | `ws://127.0.0.1:3001` | NapCat WebSocket 服务端 |
| `NAPCAT_HTTP_URL` | `http://127.0.0.1:3000` | NapCat HTTP 服务端 |
| `NAPCAT_ACCESS_TOKEN` | (必填) | OneBot 鉴权 token |
| `NAPCAT_SELF_ID` | (必填) | 机器人 QQ 号 |
| `NAPCAT_ALLOWED_USERS` | 空 | 工具白名单 QQ，逗号分隔 |
| `NAPCAT_ALLOWED_GROUPS` | 空 | 工具白名单群，逗号分隔 |
| `NAPCAT_HOME_USER` | 空 | 主人 QQ（cronjob/通知默认收件人）|
| `NAPCAT_HOME_GROUP` | 空 | 主人群 |
| `NAPCAT_ALLOW_ALL_USERS` | `false` | 是否允许所有人聊天（仍受工具白名单限制） |

---

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ) — 优秀的 OneBot v11 实现
- [Hermes Agent](https://hermes-agent.nousresearch.com) — by Nous Research

## License

MIT
