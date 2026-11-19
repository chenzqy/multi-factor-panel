## 这个 PR 做了什么

<!-- 一句话说明 -->

## 为什么

<!-- 关联 Issue：#123；或说明动机 -->

## 改动类型

- [ ] 新功能
- [ ] Bug 修复
- [ ] 新数据源 / 接口适配
- [ ] 界面调整
- [ ] 文档

## 验证方式

<!-- 你怎么确认它可用？贴命令、截图或输出 -->

```bat
python -m py_compile desktop_app.py mcp_server.py
python desktop_app.py
```

## 检查清单

- [ ] 没有提交 `build/`、`dist/`、`.venv*`、`logs/`、`config/ui_state.json`
- [ ] 没有提交填了真实 API Key 的 `watchlist.yaml`
- [ ] 新增网络请求都有超时与异常兜底
- [ ] 补充了 README 的「数据来源」表格（如果新增了接口）
