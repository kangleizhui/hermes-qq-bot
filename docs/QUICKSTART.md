# 🚀 朋友使用教程（小白版）

> 你看到的这个文档，是写给**完全不懂技术**的朋友的。跟着做一定能成。

---

## 📋 你需要准备什么？

### 1. 一台云服务器（必须）

**推荐配置**：
- 系统：Debian 12/13 或 Ubuntu 22.04/24.04
- 配置：2核 2G 内存起步（够用了）
- 带宽：1Mbps 就够

**新手友好的服务器选择**：
| 厂商 | 价格 | 推荐链接 |
|------|------|----------|
| **腾讯云轻量** | 约 ¥80/年（学生）| https://cloud.tencent.com/act/pro/lighthouse |
| **阿里云轻量** | 约 ¥90/年（新用户）| https://www.aliyun.com/product/swas |
| **雨云** | ¥3/月起 | https://www.rainyun.com |

> ⚠️ 别买国内服务器装 QQ 机器人没问题，但**装 Hermes 需要访问外网 AI**，所以最好选**境外节点**（香港/日本/新加坡）或者配好 VPN。

### 2. 一个 AI 大模型 API Key（必须）

机器人的"大脑"需要 AI 提供，二选一：

**推荐 A：火山引擎方舟（国内可用，便宜）** ⭐
- 注册：https://www.volcengine.com/product/ark
- Coding Plan 套餐：¥30/月起，包月不限量
- 适合中国大陆服务器

**推荐 B：OpenRouter（境外）**
- 注册：https://openrouter.ai
- 按用量付费，充 $5 能用很久
- 适合境外服务器

### 3. 一个 QQ 小号（必须）

⚠️ **别用你天天聊天的主号**！建议申请一个新号专门给机器人用。
- 注册：https://ssl.zc.qq.com/v3/index-chs.html

---

## 🛠️ 部署步骤（30 分钟搞定）

### 第一步：连上你的服务器

买完服务器，控制台会给你：
- 公网 IP（比如 `1.2.3.4`）
- root 密码

**Windows 用户**：下载 [MobaXterm](https://mobaxterm.mobatek.net/download.html) 或 [FinalShell](http://www.hostbuf.com/t/988.html)

**Mac 用户**：打开终端，运行：
```bash
ssh root@你的服务器IP
```

### 第二步：装 Hermes Agent

复制粘贴这段命令到服务器终端：

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

装完后配置 AI：
```bash
hermes setup
```

按提示选你的 AI 提供商（火山引擎 / OpenRouter / 其他），粘贴 API Key 即可。

> 详细文档：https://hermes-agent.nousresearch.com/docs/getting-started

### 第三步：一键部署 QQ 机器人

```bash
git clone https://github.com/kangleizhui/hermes-qq-bot.git
cd hermes-qq-bot
bash install.sh
```

脚本会问你 6 个问题，按提示填：

| 问题 | 怎么填 |
|------|--------|
| 机器人 QQ 号 | 你刚申请的小号 QQ |
| 你的 QQ 号 | 你**自己**的主号 QQ（用来指挥机器人）|
| 默认接收消息的群号 | 想让机器人进的群号，没有就回车跳过 |
| OneBot Access Token | 随便编一串字母数字，比如 `abc123xyz` |
| 工具白名单 | 回车（默认只有你能用工具）|
| 群白名单 | 回车（默认无）|

### 第四步：扫码登录小号

```bash
sudo systemctl start napcat
sudo journalctl -u napcat -f
```

屏幕上会出现一个**二维码**，用刚申请的小号 QQ 扫码登录。

登录成功后按 `Ctrl+C` 退出日志查看。

### 第五步：重启 Hermes 让它认识 QQ

```bash
sudo systemctl restart hermes-gateway
```

### 第六步：测试

用**你的主号**给**机器人小号**发一条消息，比如：
> 你好，你是谁？

机器人秒回，🎉 部署成功！

---

## 💬 常用玩法

部署完之后，你可以：

| 你说什么 | 机器人会做什么 |
|----------|----------------|
| "帮我查下今天北京天气" | 用工具上网查 |
| "你写个 Python 脚本算 1+1" | 写代码并跑给你看 |
| "明早 8 点提醒我开会" | 设定时任务 |
| "帮我写个 500 字心情日记" | 直接写 |
| "我刚才发你那张照片是啥？" | 看图回答 |
| "明天天气怎么样？" | 查询 |

---

## 🆘 遇到问题怎么办？

### 问题 1：扫码后提示"账号异常"

腾讯风控。小号刚申请的太新会被拦。**养号方法**：
- 在手机上用这个号正常聊几天天
- 加几个群进去说几句话
- 等 3-7 天后再扫码登录服务器

### 问题 2：机器人不回消息

按顺序检查：
```bash
# 1. NapCat 在跑吗？
sudo systemctl status napcat

# 2. Hermes 在跑吗？
sudo systemctl status hermes-gateway

# 3. 看错误日志
sudo journalctl -u hermes-gateway -n 50
```

### 问题 3：群里 @ 机器人没反应

确认你在 `install.sh` 里把那个群号填进了**群白名单**。没填的话重新跑一次：

```bash
nano ~/.hermes/.env
```

找到 `NAPCAT_ALLOWED_GROUPS=` 这行，把群号填上（多个用逗号分隔），保存后：
```bash
sudo systemctl restart hermes-gateway
```

### 问题 4：账号被冻结了

⚠️ **这就是为啥不让你用主号**。机器人行为容易触发腾讯风控。冻结了就换号，建议常备 2-3 个小号轮换。

---

## 🤝 想找人帮忙？

- 提 issue：https://github.com/kangleizhui/hermes-qq-bot/issues
- Hermes 官方文档：https://hermes-agent.nousresearch.com/docs

---

## ⚖️ 免责声明

- QQ 没有官方机器人 API，本项目使用第三方协议库
- 使用风险**自负**，账号被封不负责
- 不要用于骚扰、刷屏、商业营销
- 仅供个人学习使用
