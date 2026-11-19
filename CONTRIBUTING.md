# 贡献指南

感谢你愿意改进这个项目！下面是几条约定，遵循它们可以让合并过程更顺利。

## 开发环境

```bat
git clone <your-fork-url>
cd multi_factor_panel
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python desktop_app.py
```

- Python 3.10+
- 桌面 UI 使用 PySide6（Qt Widgets + QtCharts），请避免引入其他 GUI 框架
- 需要 `mcp<2.0`（`mcp_server.py` 用的是 1.x 的 `FastMCP`）

## 提交前自检

1. `python -m py_compile desktop_app.py mcp_server.py src\*.py` 无报错
2. 双击 / 命令行启动程序，因子表能出数、切市场不崩
3. 不要提交以下内容（`.gitignore` 已覆盖）：
   - `build/`、`dist/`、`.venv*`、`__pycache__/`、`logs/`
   - `config/ui_state.json`（界面设置，运行时生成）
   - 填了真实 API Key 的 `watchlist.yaml`
   - `*.lnk`、`*.bak`、编辑器临时文件

## 代码风格

- 中文注释、英文标识符；公开函数写简短 docstring
- 网络请求必须有超时与异常兜底，不允许让异常冒到 UI 线程
- 新增数据源时**保留降级链**：主源 → 备用源 → 本地代码表，并在 `attrs["source"]` 标注实际来源
- 新增界面元素时优先复用 `src/uistate.py` 做持久化

## 新增数据源的推荐做法

1. 在对应模块（`data_source.py` / `options.py` / `crypto.py` / `chartdata.py` / `news.py`）里加私有函数 `_fetch_xxx()`，返回统一列名
2. 用 `time.time()` + 模块级缓存控制请求频率（参考 `crypto.CACHE_TTL`、`options.CACHE_TTL`）
3. 在 README 的「数据来源」表格补一行，注明字段口径与已知坑（比如「`f23` 才是 PB×100」这类经验非常有价值）

## 提交信息

参考 Conventional Commits：

```
feat: 增加科创板期权链支持
fix: 修复美股 BRK-B 代码取不到价格
docs: 补充期权隐含远期的说明
refactor: 抽出统一的行情降级逻辑
```

## Pull Request

- 一个 PR 只做一件事，附上改动原因与验证方式
- 涉及界面的改动请附截图
- 涉及接口字段的改动请说明测试所用的 akshare / yfinance 版本

## 行为准则

保持技术讨论的礼貌与具体：描述问题给复现步骤，提建议给理由。不接受人身攻击与广告。
