# Security policy

## 凭据

不要在 issue、pull request、YAML、CSV、命令、日志或测试夹具中提交 API key、token、cookie、Authorization header 或私钥。

Hosted DiffDock 只从环境变量 `NVIDIA_API_KEY` 读取凭据。怀疑凭据曾经暴露时，应先在服务端吊销并轮换，再执行新的 hosted request。

## 外部服务与敏感结构

- 默认不允许上传专有、未公开或敏感结构。
- Hosted DiffDock 需要显式上传授权，以及 `public` 或 `non-sensitive` 数据分类。
- 自托管后端仍需单独核对容器、模型、数据和基础设施许可。
- 未通过授权检查时，`run` 必须阻断。

## 命令执行

- 仅执行 schema-valid、预检通过且经过审阅的 `argv` 数组。
- 子进程使用 `shell=false`。
- 禁止 `Invoke-Expression`、`shell=True`、自动安装、自动接受条款和未审阅的删除操作。
- 输出路径必须位于声明的 package root 内。

## 报告问题

请不要通过公开 issue 披露真实凭据、专有结构或可复现的敏感数据。先联系仓库维护者，提供最小化、已脱敏的复现材料。
