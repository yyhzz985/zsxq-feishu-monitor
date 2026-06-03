# QQ群消息 → 飞书群转发系统

本目录是 QQ 群监控转发系统。它把指定 QQ 群的新消息实时同步到飞书群，支持文本、图片、文件、语音、便签图渲染、去水印和失败告警。

## 当前状态

已上线运行，分两类服务：

- `qq_feishu_bridge.py`：Docker 内运行，负责多 QQ 群直接转发。
- `bridge_wu2198.py`：宿主机 systemd 运行，负责 wu2198 群的便签图渲染转发。

## 架构

```text
QQ群
  → NapCat Docker（OneBot v11 WebSocket）
  → Python Bridge
  → 飞书 OpenAPI
  → 测试/正式飞书群
```

## 转发规则

- 文本：直接发飞书。
- 图片：下载后上传飞书；需要去水印的群先走 `services/media_processing_service.py`。
- 文件/语音：下载后上传飞书文件。
- wu2198：文字和图片先渲染成便签图，再发飞书图片。
- `forward/json/xml` 群聊合集、小程序卡片等复杂消息跳过。
- 告警只发测试/告警群，正式群只接收内容。

## 项目结构

```text
qq-feishu-bridge/
├── qq_feishu_bridge.py              # Docker 多群桥接主程序
├── bridge_wu2198.py                 # wu2198 便签图桥接
├── config.example.json              # 配置示例，不含真实群和凭据
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml           # 部署示例，只放占位符
│   ├── .env.template                # 私有环境变量模板
│   └── requirements.txt
├── services/
│   └── media_processing_service.py  # 图片处理业务逻辑
├── utils/
│   └── watermark_cleaner.py         # 粉色斜体水印清理工具
├── tests/
│   ├── fixtures/                    # 单元测试图片夹具
│   └── test_*.py
└── docs/
    ├── 可行性方案.md
    └── 开发日志.md
```

## 配置

复制 `deploy/.env.template` 到服务器私有 `.env`，只在服务器填写真实值。

必须配置：

```bash
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
NAPCAT_WS=ws://napcat:3001
```

多群映射放在私有 `config.json`，参考 `config.example.json`。真实 `config.json`、`.env`、NapCat 登录数据、数据库和日志都不要提交。

## 部署

Docker 多群桥接：

```bash
cd /opt/qq-feishu-bridge
docker compose -f deploy/docker-compose.yml up -d --build bridge
```

wu2198 宿主机服务：

```bash
systemctl restart qq-bridge-wu2198.service
systemctl status qq-bridge-wu2198.service --no-pager
```

## 验证

本地单元测试：

```bash
python -m unittest discover -s tests
```

服务器运行检查：

```bash
docker ps
docker logs qq-feishu-bridge --tail 80
systemctl is-active qq-bridge-wu2198.service
journalctl -u qq-bridge-wu2198.service -n 80 --no-pager
```

不要只看容器 `Up`。必须同时确认飞书 token、NapCat WebSocket、最近转发记录。
