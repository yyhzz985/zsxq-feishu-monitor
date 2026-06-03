# QQ群 → 飞书群 消息转发部署指南

## 目录结构

```
qq-feishu-forwarder/
├── docker-compose.yml        # 主配置文件
├── forwarder/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── forwarder.py          # 转发脚本
└── napcat_data/              # 自动生成，QQ登录数据
```

---

## 第一步：服务器准备

SSH 登入阿里云服务器后，安装 Docker：

```bash
# Ubuntu / Debian
curl -fsSL https://get.docker.com | sh
systemctl enable docker && systemctl start docker

# 验证安装
docker --version
docker compose version
```

---

## 第二步：上传项目文件

在服务器上创建目录并上传文件：

```bash
mkdir -p ~/qq-feishu-forwarder/forwarder
cd ~/qq-feishu-forwarder
```

把本地的三个文件上传到服务器对应位置（scp 或直接粘贴内容）。

---

## 第三步：修改配置

编辑 `docker-compose.yml`，替换以下三处：

| 占位符 | 替换为 |
|--------|--------|
| `你的QQ号` | 要用来监控的QQ小号 |
| `替换成你的token` | 飞书群机器人 Webhook URL 最后的 token 部分 |
| `替换成要监控的QQ群号` | 目标QQ群的群号（纯数字） |

> 飞书 Webhook 格式：`https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx`
> 在飞书群设置 → 群机器人 → 添加自定义机器人 中获取。

---

## 第四步：启动服务

```bash
cd ~/qq-feishu-forwarder

# 首次启动（会自动拉取镜像、构建转发脚本容器）
docker compose up -d

# 查看 NapCat 启动日志，等待二维码出现
docker logs -f napcat
```

---

## 第五步：扫码登录QQ

⚠️ 关键步骤，有两种方式：

### 方式A：WebUI 扫码（推荐）

1. 浏览器访问 `http://你的服务器IP:6099`
2. 用手机QQ扫描网页上的二维码登录

> 如果访问不了，先在阿里云控制台安全组开放 **6099** 端口。

### 方式B：日志中的二维码

```bash
docker logs napcat 2>&1 | grep -A 20 "qrcode"
```

---

## 第六步：配置 NapCat WebSocket

登录 WebUI 后：

1. 进入 **网络配置**
2. 点击 **新建** → 选择 **WebSocket 服务（正向）**
3. 端口填 `3001`，保存
4. 确认状态变为绿色"已启用"

---

## 第七步：验证转发

在目标 QQ 群发一条测试消息，观察日志：

```bash
# 查看转发脚本日志
docker logs -f qq-feishu-forwarder

# 预期输出：
# 2025-xx-xx [INFO] ✅ 已连接 NapCat，开始监听群消息...
# 2025-xx-xx [INFO] 群xxxxxxx | 某某某: 测试消息内容
# 2025-xx-xx [INFO] 飞书发送成功: 【QQ群消息】...
```

---

## 常用运维命令

```bash
# 查看所有容器状态
docker compose ps

# 重启某个容器
docker compose restart forwarder

# 停止所有服务
docker compose down

# 更新转发脚本后重新构建
docker compose up -d --build forwarder

# 实时查看日志
docker compose logs -f
```

---

## 常见问题

### Q：登录时提示"网络环境不稳定"

阿里云服务器 IP 和手机 QQ 不在同一地区会触发此提示。
**解决：** 先在本地电脑登录 QQ，然后把配置目录传到服务器：

```bash
# macOS/Linux 本地执行
scp -r ~/.config/QQ root@服务器IP:~/qq-feishu-forwarder/napcat_data/
```

Windows 的 QQ 配置在：`C:\Users\你的用户名\Documents\Tencent Files`

---

### Q：转发脚本一直显示"无法连接 NapCat"

说明 NapCat 还没启动完成或 WebSocket 端口没配置。
检查步骤：
1. `docker logs napcat` 确认 NapCat 正常运行
2. 在 NapCat WebUI 确认已开启正向 WebSocket（端口3001）

---

### Q：飞书收不到消息但日志显示成功

检查飞书机器人的 Webhook URL 是否正确，以及机器人是否已加入目标群。

---

### Q：QQ账号被风控/封号

使用专用小号，降低消息监听频率触发的风险。
如被风控，在手机QQ申诉解封，选"账号异常登录"。

---

## 消息类型支持

| 消息类型 | 转发效果 |
|---------|---------|
| 文本 | ✅ 完整转发文字内容 |
| 图片 | ✅ 转发图片直链URL |
| 语音 | ⚠️ 显示 [语音消息]（飞书不支持QQ语音格式）|
| 文件 | ✅ 显示文件名 |
| @成员 | ✅ 显示被@的群名片/QQ号 |
| 小程序卡片 | ✅ 显示标题 |
| 合并转发 | ⚠️ 显示 [合并转发消息] |
