# Contributing

本项目接受针对 `baker-protein-design` 的可审计改进。提交内容应保持任务路由、计算阶段、证据等级和实验验证相互独立。

## 提交前检查

1. 说明改动对应的 route 和 mode。
2. 对 schema、输入哈希、seed、工具版本、许可或输出字段的变化补充测试。
3. 数值阈值必须包含来源、locator、任务和阶段，不得设为跨任务通用结论。
4. 不提交论文全文、微信材料、外部代码、真实凭据、模型权重或许可证不允许再分发的数据。
5. 不允许自动安装、任意 shell 字符串、凭据持久化或未授权 hosted upload。

## 本地测试

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:BAKER_DESIGN_TEST_ALLOW_NETWORK = "0"
python -m unittest discover `
  -s .\skills\baker-protein-design\scripts `
  -p "test_*.py" `
  -v
```

测试完成后确认 `skills/` 中没有 `__pycache__` 或 `.pyc`。

## 文档与来源

- 优先链接官方仓库、DOI、官方服务文档和 RCSB 条目。
- 新增方法时，同时填写输入、输出、指标角色、许可边界和实现状态。
- 预测结果使用 `computational_prediction`，实验结果需要样本、assay、日期和原始数据来源。
- 更新日期化工具快照时，保留 commit、commit date、许可证 URL 和检查日期。
