# ZSXQ Feishu Monitor

知识星球 → 飞书群 自动化内容推送系统。每分钟轮询指定星球的新帖，生成便签图，通过飞书 OpenAPI 推送到群。

本仓库同时包含 QQ 群 → 飞书群转发系统，入口在 `qq-feishu-bridge/`。

## 项目结构

```
├── src/
│   └── zsxq_monitor.py        # 主脚本（SQLite 状态机 + OpenAPI 发送）
├── deploy/
│   ├── zsxq-poll.service      # systemd oneshot service
│   ├── zsxq-poll.timer        # systemd timer（每分钟 + 开机自启）
│   ├── zsxq-poll.env          # .env 配置模板
│   ├── setup_server.sh        # 阿里云一键初始化脚本
│   ├── install_windows_task.ps1  # Windows 任务计划安装脚本
│   ├── build_deploy_package.ps1  # 部署包生成脚本
│   └── validate_deploy_bundle.py # 部署包校验脚本
├── docs/
│   ├── 服务器部署手册.md       # 服务器运维完整指南
│   ├── 本地定时任务_使用手册.md # Windows 本地开关指南
│   ├── 开发日志.md             # 完整开发记录
│   └── 部署与切换手册.md       # 部署与发送模式切换
├── qq-feishu-bridge/           # QQ 群监控转发系统
│   ├── qq_feishu_bridge.py      # Docker 多群桥接
│   ├── bridge_wu2198.py         # wu2198 便签图桥接
│   ├── deploy/                  # Docker 部署模板
│   └── tests/                   # 单元测试与图片夹具
└── .gitignore
```

## 快速开始

### 本地运行

```bash
# 环境自检
python src/zsxq_monitor.py --check

# 试运行（不写库不推送）
python src/zsxq_monitor.py --dry-run

# 查看状态
python src/zsxq_monitor.py --status
```

### 服务器部署

详见 `docs/服务器部署手册.md`。

```bash
# 一键初始化（阿里云 Linux）
chmod +x deploy/setup_server.sh
sudo ./deploy/setup_server.sh

# 配置凭据
sudo nano /opt/zsxq-monitor/config/.env

# 启动
sudo systemctl enable --now zsxq-poll.timer
```

### Windows 本地定时

详见 `docs/本地定时任务_使用手册.md`。

```powershell
# 安装任务计划
.\deploy\install_windows_task.ps1

# 开关
Enable-ScheduledTask -TaskName 'ZSXQ-Feishu-Monitor'
Disable-ScheduledTask -TaskName 'ZSXQ-Feishu-Monitor'
```

## 核心特性

- **SQLite 状态机**：每条帖子有 `discovered → rendered → sent/failed` 状态流转，不重不漏
- **飞书 OpenAPI**：无需 Node.js / lark-cli，直接通过飞书自建应用发送
- **幂等键**：飞书消息携带 `zsxq-topic-{topic_id}` 幂等键，重试不重复
- **1059 自适应**：ZSXQ API 限流时自动退避重试，5 次 + 递增间隔
- **多环境支持**：同一脚本同时支持 Linux systemd 和 Windows 任务计划
- **故障自恢复**：锁文件 PID 检测自动清理，离线不丢帖不重复

## QQ 群转发

详见 `qq-feishu-bridge/README.md`。

支持：

- NapCat WebSocket 接入 QQ 群消息
- 多 QQ 群映射到测试/正式飞书群
- 图片、文件、语音转发
- 指定群图片去水印
- wu2198 便签图渲染转发
- 测试群告警和正式群内容分离

## 配置

复制 `deploy/zsxq-poll.env` 为 `.env` 并填写：

```bash
ZSXQ_ACCESS_TOKEN=       # 知识星球 Cookie token
FEISHU_APP_ID=            # 飞书自建应用 App ID
FEISHU_APP_SECRET=        # 飞书自建应用 App Secret
FEISHU_CHAT_ID=           # 飞书群 chat_id
FEISHU_CONTENT_CHAT_IDS=  # 可选，额外内容群，多个用英文逗号分隔
FEISHU_ALERT_CHAT_ID=     # 可选，测试/告警群；告警和健康日报只发这里
FEISHU_SEND_MODE=openapi  # 发送模式：openapi 或 cli
WATERMARK_TEXT=           # 水印文字
ZSXQ_SAVE_DIR=            # 图片存档目录
```

发送规则：星球图片和附件会发到 `FEISHU_CHAT_ID`、`FEISHU_CONTENT_CHAT_IDS`、`FEISHU_ALERT_CHAT_ID`；告警和健康日报只发到 `FEISHU_ALERT_CHAT_ID`。如果没配置 `FEISHU_ALERT_CHAT_ID`，告警会发到 `FEISHU_CHAT_ID`。

## 已知限制

- ZSXQ API 为非官方接口，可能偶尔返回 1059 错误，脚本会自动重试
- ZSXQ 可能变更 API 返回结构，脚本已做多字段兼容（large/original/thumbnail）
- 需飞书自建应用具备：群消息、图片上传、文件上传权限

## License

MIT
