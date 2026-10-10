# Lumen HTML 工作流

用标准 JSON 组织门户、报告和教学页。先确定资料范围与结论，再构建、编辑、保存和读回。

```sh
python engine/entry.py schema --json
python engine/entry.py --state-dir ./state build --content examples/content.json --kind portal --json
```

每个成果保留来源和版本。示例是虚构资料，模型生成需要使用者自己的官方 CLI 配置。
