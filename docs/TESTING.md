# 测试与评估

安装锁定依赖后运行 `python -m pytest -q`。未配置 PostgreSQL 时为 41 项通过、6 项跳过；配置专用数据库后可运行全部 47 项。

覆盖身份和会话撤销、CSRF、租户与角色过滤、审批防重放、幂等、限流、并发控制、文档解析与回滚、旧请求隔离，以及流式文本和工具参数分片。自动化测试使用模拟模型，不需要真实模型 Key。

## PostgreSQL 集成测试

```bash
docker run -d --name desk-test-db -p 127.0.0.1:55439:5432 -e POSTGRES_PASSWORD=test-only-password -e POSTGRES_DB=desk_validation pgvector/pgvector:pg16
```

将 `TEST_POSTGRES_URL` 设置为 `postgresql://postgres:test-only-password@127.0.0.1:55439/desk_validation` 后执行 pytest。

Windows 使用 `$env:TEST_POSTGRES_URL='...'`；Linux/macOS 使用 `export TEST_POSTGRES_URL='...'`。

**测试会清空专用测试库内的应用表。** 只允许本机且名为 `desk_validation` 的数据库，不得指向业务数据库。CI 使用独立的临时 PostgreSQL 服务。

## 检索评估

填写模型配置并导入演示知识后，运行 `python scripts/evaluate_retrieval.py`，报告生成在被 Git 忽略的 `reports/` 中。

2026-09-24 的 20 个演示正例评估结果为 Hit@3=95%、MRR@3=90%，3 个负例均未命中。这不是回答准确率，也不是企业真实数据上的效果保证。

`scripts/compare_retrieval.py` 可对比重排开关，会调用自己的 Embedding 和重排服务。

## 验证范围

开发期间另执行过浏览器断线和限流恢复、账号切换、320–1440 像素布局、有限负载、数据库重启和备份恢复、重新部署后的数据保留，以及真实流式工具确认。

私人部署地址、原始日志、会话记录和对线上服务发起压测的操作脚本不随源码发布。公共 CI 不调用任何演示站或外部模型。

尚未覆盖长时间浸泡、真实大规模用户、iOS Safari 真机、完整读屏器评估、企业 SSO 或真实业务系统。
