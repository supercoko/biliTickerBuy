# biliTickerBuy 部署与使用手册

本文档仅介绍 **安装 → 配置 → 抢票 → 推送** 的操作流程，不涉及项目架构。

---

## 一、安装

以下任选一种。推荐 **A（pipx）** 或 **B（uv 源码）**。

### A. pipx 安装（最省心）

```bash
# 需要 Python >= 3.11
pip install --user pipx
pipx install bilitickerbuy

btb            # 启动 Gradio 网页 UI（默认 http://127.0.0.1:7860）
btb buy -h     # CLI 帮助
```

### B. uv 源码运行（可改代码）

```bash
git clone https://github.com/mikumifa/biliTickerBuy.git
cd biliTickerBuy

# uv 已生成 uv.lock，直接同步
uv sync
uv run python main.py
```

### C. 传统 venv + requirements.txt

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# Linux / macOS
# source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

### D. 下载预编译二进制（零依赖）

前往 [Releases](https://github.com/mikumifa/biliTickerBuy/releases) 下载 Windows / macOS 包，双击运行。

### E. Linux VPS 常驻（systemd）

```bash
pipx install bilitickerbuy

sudo tee /etc/systemd/system/btb.service <<'EOF'
[Unit]
Description=biliTickerBuy
After=network.target

[Service]
Environment=BTB_SERVER_NAME=0.0.0.0
Environment=BTB_PORT=7860
ExecStart=/root/.local/bin/btb
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable --now btb
```

服务器部署建议配 nginx + basic auth，或仅在内网/VPN 暴露端口。

---

## 二、首次配置（UI 模式）

启动后浏览器打开 `http://127.0.0.1:7860`，按标签页顺序操作。

### 1. 生成配置

**"生成配置"** 标签页：

1. **扫码登录** —— 用手机 B 站 App 扫码，cookie 会写入本地 `cookies.json`
2. **输入项目链接或 ID**（形如 `https://show.bilibili.com/platform/detail.html?id=84096`）
3. 选择 **场次 / 票档 / 购票人 / 收货地址**
4. 点 **"保存配置"**，下载生成的 `xxx.json` 文件备用

> ⚠️ 生成的 JSON 文件包含 cookie 和实名信息，**不要发到群里、不要上传 GitHub**。
> 项目 `.gitignore` 默认已忽略 `*.json` 及 `tickets/` 目录。

### 2. 推送通知配置（可选但强烈推荐）

**"操作抢票"** 标签页下方的推送配置区域，至少配一个：

| 渠道 | 适合场景 | 获取方式 |
|------|---------|---------|
| **飞书** | 团队协作、多人群聊同步 | 群设置 → 群机器人 → 添加自定义机器人 |
| Server酱 Turbo | 微信推送 | https://sct.ftqq.com/sendkey |
| PushPlus | 微信推送 | https://www.pushplus.plus/uc.html |
| Bark | iOS 推送（静音可响） | App Store 搜 Bark |
| Ntfy | 自建 / 多端 | https://ntfy.sh |

#### 飞书配置步骤

1. 飞书群 → 右上角设置 → **群机器人** → **添加机器人** → 选 **自定义机器人**
2. 自定义名称与头像
3. **安全设置**（三选一，推荐「签名校验」）：
   - **签名校验**：复制生成的 secret 备用
   - **IP 白名单**：填入你抢票机器的出口 IP
   - **关键词**：填入「抢票」等
4. 复制 Webhook URL（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`）
5. 在 biliTickerBuy UI 的 **"飞书通知配置"** 面板粘贴：
   - Webhook URL 或仅 token 部分均可
   - 如果启用了签名校验，填入 **secret**
6. 点 **"测试飞书连接"**，群里应收到测试消息

### 3. 抢票参数（"操作抢票"页面底部）

| 参数 | 建议值 | 说明 |
|------|-------|------|
| **抢票间隔** | 500–1000 ms | 开售抢新票用 300~500；捡漏用 1500+ |
| **间隔抖动** | 0.25 | ±25% 随机化，避免固定节奏被风控识别 |
| **单轮最大重试** | 200 | 每轮 prepare 后 createV2 尝试次数 |
| **🔍 捡漏模式** | 按需 | 开启后「无票」不退出，持续轮询等退票 |
| **捡漏间隔** | 3000 ms | 捡漏模式下的轮询间隔，2000–5000 合理 |
| **日志显示方式** | 网页 / 终端 | Windows 可选终端，macOS/Linux 建议网页 |

---

## 三、开始抢票（UI 模式）

1. **"操作抢票"** 页面：
   - 上传一个或多个 `config.json`（每个文件启动一个独立进程）
   - 填写抢票开始时间（点 **"自动填写"** 会读配置里的 sale_start）
   - 点 **开始抢票**
2. 每个任务会弹出独立的日志窗口（网页或终端）
3. 抢到后：
   - 弹出支付二维码（用支付宝/微信扫）
   - **飞书 / Server酱 / Bark 等**同步推送
   - 推送每 10 秒重发一次，持续 10 分钟（B 站订单保留时间）

**⚠️ 订单保留 10 分钟，务必在手机上待命扫码付款。**

---

## 四、命令行模式（CLI）

适合服务器部署、脚本化、定时任务。

### 基础用法

```bash
# 开售抢新票（快速）
btb buy ./my_ticket.json \
  --time_start "2026-05-01T20:00:00" \
  --interval 500 \
  --interval_jitter 0.25 \
  --max_retries 300 \
  --feishu_webhook "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx" \
  --feishu_secret "your_secret"

# 捡漏退票（慢而稳，可跑数小时）
btb buy ./my_ticket.json \
  --interval 1500 \
  --interval_jitter 0.3 \
  --scavenge_mode \
  --scavenge_interval 3000 \
  --max_retries 500 \
  --feishu_webhook "xxxxxxxx"

# 使用代理池（多 IP 轮换，触发 412 自动切换）
btb buy ./my_ticket.json \
  --https_proxys "http://127.0.0.1:7890,http://proxy2:8080,none"
```

### 常用选项

```
--time_start           ISO 时间，如 2026-05-01T20:00:00
--interval             下单间隔（ms），默认 1000
--interval_jitter      抖动比例 0~0.6，默认 0.25
--max_retries          单轮最大重试，默认 200
--scavenge_mode        捡漏模式开关
--scavenge_interval    捡漏模式间隔（ms），默认 3000
--https_proxys         代理池，逗号分隔，可包含 "none" 表示直连
--feishu_webhook       飞书机器人 Webhook
--feishu_secret        飞书签名（可选）
--pushplusToken        PushPlus token
--barkToken            Bark token
--hide_random_message  关闭失败时的群友语录
```

完整帮助：`btb buy -h`

### 环境变量替代（服务器部署推荐）

```bash
export BTB_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
export BTB_FEISHU_SECRET="your_secret"
export BTB_INTERVAL_JITTER=0.3
export BTB_SCAVENGE_MODE=true
export BTB_SCAVENGE_INTERVAL=3000
export BTB_MAX_RETRIES=300
export BTB_HTTPS_PROXYS="http://p1:8080,none"

btb buy ./my_ticket.json --time_start "2026-05-01T20:00:00"
```

---

## 五、反风控与成功率建议

### 避免触发 412 风控
- **不要**把 `--interval` 调低于 300ms
- **始终开启抖动**（`--interval_jitter` ≥ 0.2），避免固定节奏
- 使用 **代理池** + 多 IP，412 时会自动切换并指数退避
- 单账号不要同时开多进程

### 提高抢新票成功率
- 华东/华北 **低延迟 VPS**（到 `show.bilibili.com` RTT 2~5ms）
- 开售前 **1 小时** 重新扫码，保证 cookie 新鲜
- 正确设置 `--time_start`，让程序自动 NTP 校时倒计时，最后 500ms 会自旋确保首包精度
- 多账号家人号并行，每个绑不同代理和购票人

### 捡漏退票
- 开 `--scavenge_mode`
- 间隔 2000–5000ms，**慢而稳**——退票释放窗口很短但随机，守株待兔
- 配合飞书通知，人可以离开电脑

---

## 六、目录与文件

运行后会在项目目录生成：

| 路径 | 用途 | 是否被 ignore |
|------|------|-------------|
| `cookies.json` | 登录 cookie | ✅ |
| `config.json` | TinyDB 配置（含各通知 token） | ✅ |
| `btb_logs/` | 运行日志（含请求头） | ✅ |
| `tmp/` | Gradio 临时文件 | ✅ |
| `tickets/` | 约定的用户配置目录 | ✅ |
| `btb_runs/` | 托管任务状态 | ✅ |

**以上均已在 `.gitignore` 中，不会被提交。**

---

## 七、常见问题

**Q: 登录后显示"未登录"？**
A: cookie 过期。重新扫码。服务器部署的话，每次抢票前 1 小时重登一次。

**Q: 大量 412 怎么办？**
A: 立即停止，增大 `--interval`，开启抖动，准备代理池；换 IP 后再重试。

**Q: 飞书测试按钮点了没反应？**
A: 检查 Webhook 是否含 `https://`；若开启签名校验，secret 必须填对。失败信息会显示状态码和响应体。

**Q: 捡漏模式能跑多久？**
A: 程序不会主动退出。建议开飞书通知后放服务器上跑，抢到或手动停止为止。注意长时间运行需要 cookie 保持有效。

**Q: Windows 下 `btb` 命令找不到？**
A: pipx 安装后需要 `pipx ensurepath` 并重开终端；或直接 `python main.py`。

---

## 八、免责声明

本工具仅供**个人学习研究**。请勿用于商业牟利、代抢或违反平台规则的用途。所有请求速率均由用户自行控制，开发者与项目对使用后果不承担责任。

详见根目录 [LICENSE](LICENSE) 及 [README.md](README.md)。
