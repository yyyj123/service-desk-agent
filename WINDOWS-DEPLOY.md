# Windows 双击部署

1. 在另一台 Windows 电脑安装并启动 [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/)，完成 WSL 2 / 虚拟化设置，使用 Linux 容器。Docker Compose 需 2.24.4 或更新版本。
2. 从 GitHub 选择 **Code → Download ZIP**，解压整个项目到可写目录。不要在压缩包里运行，不要只复制启动文件。无需安装 Git、Python 或编译工具。
3. 双击 **部署并启动.cmd**。首次会创建 `.env` 并打开记事本：填入自己的 `IT_CHAT_KEY`、`IT_EMBED_KEY`，核对模型名称和对应的 `IT_CHAT_URL`、`IT_EMBED_URL`。保存后回到启动窗口按回车。模型 API Key 需要向你使用的供应商获取，脚本不会生成有效的供应商密钥。
4. 等待镜像构建、账号初始化及知识库向量化。成功后自动打开 `http://127.0.0.1:8600`；员工与管理员的随机密码在根目录 `runtime/accounts.txt`。
5. 下次继续双击同一文件；双击 **停止服务.cmd** 停止此部署并保留账号、知识库和对话。

本入口固定 Compose 项目名 `service-desk-desktop`，复用原 `compose.yaml`，只绑定本机 8600 端口。首次自动生成 `.env.enterprise` 的 JWT 与指标密钥。已有配置、账号和数据保持原样。初始化与向量化全部在容器内执行，无需在宿主机运行 Python。监控服务仍按原文档单独启用；本入口不启用 monitoring profile。

默认使用 SQLite/Chroma，数据在 Docker 的命名卷中；不要使用 `down -v` 删除卷。`.env`、`.env.enterprise`、`runtime/accounts.txt` 包含本机私密配置，不要上传或随源码转发。复制源码到新电脑会创建新的数据和账号，不会自动迁移旧电脑的数据。每台电脑建议只保留一个此入口的实例。

首次向量化会调用配置的 Embedding 服务，可能消耗额度。只有完整入库成功后才保存本机初始化标记，失败后可重试；相同 Embedding 配置的后续启动跳过该步骤。更新仓库内置知识文件后，需在启动成功后手动执行以下命令重新入库：

```powershell
docker compose -p service-desk-desktop -f compose.yaml run --rm app python -m service.manage ingest
```

如果在 `.env` 配置 PostgreSQL，地址必须从容器内可达；Windows 宿主机数据库通常使用 `host.docker.internal`，不能用容器自身的 `localhost`。更换数据库不会自动迁移账号和向量，请按原文档处理。

失败窗口会保留原因。检查 Docker Desktop / Linux engine、8600 端口、镜像与 pip 下载网络、模型 Key / URL / 模型名称和余额。Embedding 初始化失败后修正配置再双击，不能通过跳过入库完成可用部署。容器日志在 Docker Desktop 的 `service-desk-desktop` 项目中查看。

高级命令（仓库根目录）：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\desktop-deploy.ps1 -NoBrowser
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\desktop-deploy.ps1 -Action Stop
```
