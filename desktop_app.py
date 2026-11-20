"""
多因子监控面板 —— 原生桌面版（PySide6，不打开浏览器）。

启动方式（Windows）：
    双击 run_windows.bat
或命令行：
    python desktop_app.py

打包为独立 exe：
    双击 build_windows.bat
"""

from __future__ import annotations

import sys
from datetime import datetime

import pandas as pd
from PySide6 import QtCharts, QtCore, QtGui, QtWidgets

from src import chartdata, crypto, data_source, factors, screener, options, news, ai_assistant, uistate


# 表格展示列与表头
DISPLAY_COLS = [
    "rank", "code", "name", "price", "change_pct", "total_score", "grade",
    "valuation_score", "momentum_score", "quality_score",
    "volatility_score", "liquidity_score",
]
HEADERS = [
    "排名", "代码", "名称", "最新价", "涨跌幅%", "综合分", "评级",
    "估值", "动量", "质量", "波动", "流动性",
]

GRADE_COLOR = {
    "强势": "#d4edda",
    "偏强": "#e2f0d9",
    "中性": "#fff3cd",
    "偏弱": "#f8d7da",
}

# 评级徽标配色（文字色 / 底色）
GRADE_BADGE = {
    "强势": ("#0f6b2f", "#c8ecc9"),
    "偏强": ("#2f7d43", "#dcf0d9"),
    "中性": ("#8a6d00", "#fdf0c9"),
    "偏弱": ("#a13333", "#fbdcdc"),
}

# 表头说明，鼠标悬停即知每个因子的含义
HEADER_TIPS = [
    "综合得分排名（1 为最优）",
    "证券代码",
    "证券名称",
    "最新价（元）",
    "当日涨跌幅%，红涨绿跌",
    "综合得分：五个因子加权，0~100",
    "评级：强势≥80／偏强≥60／中性≥40／偏弱<40",
    "估值因子：PE、PB 越低分越高，亏损股记 0 分",
    "动量因子：近 20 个交易日涨幅越高分越高",
    "质量因子：ROE 越高分越高",
    "波动因子：20 日波动率越低分越高（越稳健）",
    "流动性因子：成交额越大分越高",
]

FACTOR_COLS = ("valuation_score", "momentum_score", "quality_score",
               "volatility_score", "liquidity_score")

FACTOR_NAMES = {
    "valuation_score": "估值",
    "momentum_score": "动量",
    "quality_score": "质量",
    "volatility_score": "波动",
    "liquidity_score": "流动性",
}

COL_WIDTHS = {
    "rank": 52, "code": 76, "name": 96, "price": 78, "change_pct": 80,
    "total_score": 84, "grade": 62,
}


class NumericItem(QtWidgets.QTableWidgetItem):
    """按数值排序的单元格：显示文本可以带装饰，排序始终用真实数值。"""

    def __init__(self, text: str, value=None) -> None:
        super().__init__(text)
        self._value = value

    def __lt__(self, other) -> bool:  # noqa: D105
        a = self._value
        b = getattr(other, "_value", None)
        if a is None and b is None:
            return self.text() < other.text()
        if a is None:
            return False
        if b is None:
            return True
        return a < b


def score_bar(score) -> str:
    """把 0~100 的分数画成 5 格条形，比纯数字直观。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return ""
    if s != s:
        return ""
    filled = int(round(max(0.0, min(100.0, s)) / 20.0))
    return "█" * filled + "░" * (5 - filled)


class NewsWorker(QtCore.QThread):
    """后台线程拉取新闻，避免网络请求阻塞界面。"""

    done = QtCore.Signal(object)

    def __init__(self, sources: list, rss_feeds: list, limit: int) -> None:
        super().__init__()
        self.sources = sources
        self.rss_feeds = rss_feeds
        self.limit = limit

    def run(self) -> None:
        self.error = None
        try:
            self.result = news.fetch_news(
                source_keys=self.sources, rss_feeds=self.rss_feeds, limit=self.limit
            )
        except Exception as exc:
            self.result = pd.DataFrame()
            self.error = repr(exc)
        self.done.emit(self.result)


class OptionWorker(QtCore.QThread):
    """后台线程拉取期权链，避免主线程在 5~10 秒里假死。"""

    done = QtCore.Signal(object, object, str)  # (chain, info, 错误信息)

    def __init__(self, market: str, expiry: str) -> None:
        super().__init__()
        self.market = market
        self.expiry = expiry

    def run(self) -> None:
        try:
            chain = options.fetch_option_chain(market_key=self.market, expiry=self.expiry)
            info = options.chain_summary(market_key=self.market, expiry=self.expiry)
            self.done.emit(chain, info, "")
        except Exception as exc:
            self.done.emit(None, None, repr(exc))


class UniverseWorker(QtCore.QThread):
    """后台线程拉取行情，避免同步网络请求把主线程卡住十几秒。"""

    done = QtCore.Signal(object, str, str)  # (DataFrame, 阶段, 错误信息)

    def __init__(self, kwargs: dict, phase: str) -> None:
        super().__init__()
        self.kwargs = kwargs
        self.phase = phase

    def run(self) -> None:
        try:
            frame = data_source.get_universe(**self.kwargs)
            self.done.emit(frame, self.phase, "")
        except Exception as exc:
            self.done.emit(None, self.phase, repr(exc))


class AiWorker(QtCore.QThread):
    """后台线程调用 DeepSeek，避免网络请求阻塞界面。"""

    done = QtCore.Signal(str, str)  # (回复文本, 错误信息)

    def __init__(self, messages: list, cfg: dict, api_key: str) -> None:
        super().__init__()
        self.messages = messages
        self.cfg = cfg
        self.api_key = api_key

    def run(self) -> None:
        try:
            text, error = ai_assistant.chat(self.messages, self.cfg, api_key=self.api_key)
        except Exception as exc:
            text, error = "", repr(exc)
        self.done.emit(text or "", error or "")


class ChartWindow(QtWidgets.QWidget):
    """个股分时 / 日线折线图窗口，支持自动刷新与跟随选股。"""

    MODES = [
        ("当日分时（5 分钟）", "intra", 5, 60),
        ("当日分时（15 分钟）", "intra", 15, 20),
        ("日线（近 120 日）", "daily", 0, 120),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent, QtCore.Qt.Window)
        self.setWindowTitle("个股走势")
        self.resize(820, 500)
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, False)

        self.code = ""
        self.name = ""
        self.market = "A股"

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel("—")
        self.title.setTextFormat(QtCore.Qt.RichText)
        bar.addWidget(self.title)
        bar.addStretch(1)

        self.mode_box = QtWidgets.QComboBox()
        for label, _kind, _scale, _len in self.MODES:
            self.mode_box.addItem(label)
        self.mode_box.currentIndexChanged.connect(lambda *_: self.reload())
        bar.addWidget(self.mode_box)

        self.auto_chk = QtWidgets.QCheckBox("自动刷新(60秒)")
        self.auto_chk.setChecked(True)
        self.auto_chk.toggled.connect(self._apply_timer)
        bar.addWidget(self.auto_chk)

        self.reload_btn = QtWidgets.QPushButton("🔄 立即刷新")
        self.reload_btn.clicked.connect(lambda *_: self.reload())
        bar.addWidget(self.reload_btn)
        root.addLayout(bar)

        self.chart = QtCharts.QChart()
        self.chart.setAnimationOptions(QtCharts.QChart.NoAnimation)
        self.chart.legend().setVisible(False)
        self.chart.setBackgroundRoundness(6)
        self.chart.setBackgroundBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
        self.chart.setPlotAreaBackgroundBrush(QtGui.QBrush(QtGui.QColor("#fbfcfe")))
        self.chart.setPlotAreaBackgroundVisible(True)
        self.chart.setMargins(QtCore.QMargins(6, 6, 6, 6))

        self.view = QtCharts.QChartView(self.chart)
        self.view.setRenderHint(QtGui.QPainter.Antialiasing)
        root.addWidget(self.view, 1)

        self.hint = QtWidgets.QLabel("—")
        self.hint.setStyleSheet("QLabel{color:#5f6b7a;font-size:12px;}")
        root.addWidget(self.hint)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(lambda: self.reload(silent=True))
        self._apply_timer()

    # ------------------------------------------------------------------ 逻辑
    def _apply_timer(self) -> None:
        if self.auto_chk.isChecked():
            self.timer.start(60 * 1000)
        else:
            self.timer.stop()

    def show_for(self, code: str, name: str, market: str) -> None:
        self.code, self.name, self.market = str(code), str(name), market
        self.setWindowTitle(f"{code} {name} 走势")
        self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def follow(self, code: str, name: str, market: str) -> None:
        """主表选中变化时跟随切换（窗口已打开才生效）。"""
        code, name = str(code), str(name)
        if (code, name, market) == (self.code, self.name, self.market):
            return
        self.code, self.name, self.market = code, name, market
        self.setWindowTitle(f"{code} {name} 走势")
        self.reload()

    def reload(self, silent: bool = False) -> None:
        if not self.code:
            return
        label, kind, scale, count = self.MODES[self.mode_box.currentIndex()]
        if not silent:
            self.hint.setText("正在加载行情…")
            QtWidgets.QApplication.processEvents()
        try:
            if kind == "daily":
                frame = chartdata.fetch_daily(self.code, self.market, count)
            else:
                frame = chartdata.fetch_intraday(self.code, self.market, scale, count)
        except Exception as exc:
            self.hint.setText(f"行情获取失败：{exc}")
            return
        if frame is None or frame.empty:
            self.hint.setText(f"{self.code} 暂无行情数据")
            return
        self._draw(frame, label)

    # ------------------------------------------------------------------ 绘制
    def _draw(self, frame: pd.DataFrame, label: str) -> None:
        chart = self.chart
        chart.removeAllSeries()
        for axis in list(chart.axes()):
            chart.removeAxis(axis)

        times = pd.to_datetime(frame["time"])
        closes = frame["close"].astype(float).tolist()
        first, last = closes[0], closes[-1]
        up = last >= first
        color = "#d32f2f" if up else "#2e7d32"
        change = (last / first - 1.0) * 100 if first else 0.0

        line = QtCharts.QLineSeries()
        line.setName(self.code)
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setWidthF(2.0)
        line.setPen(pen)
        # X 轴用等距序号（1..n），而不是真实时间：这样午休、隔夜不会在图上
        # 拉出虚假的斜线，形态与行情软件的分时图一致。
        n = len(closes)
        xs = [float(i + 1) for i in range(n)]
        for x, y in zip(xs, closes):
            line.append(x, float(y))
        chart.addSeries(line)

        span_min = (times.iloc[-1] - times.iloc[0]).total_seconds() / 60.0
        if span_min > 48 * 60:
            label_fmt = "%m-%d"
        elif span_min > 6 * 60:
            label_fmt = "%m-%d %H:%M"
        else:
            label_fmt = "%H:%M"

        axis_x = QtCharts.QCategoryAxis()
        axis_x.setLabelsPosition(QtCharts.QCategoryAxis.AxisLabelsPositionOnValue)
        axis_x.setLabelsColor(QtGui.QColor("#5f6b7a"))
        axis_x.setGridLineColor(QtGui.QColor("#eef1f6"))
        axis_x.setLineVisible(True)
        axis_x.setRange(0.5, n + 0.5)
        # 标签避开左右边缘（贴边的刻度会被 Qt 直接隐藏）
        positions = (0.06, 0.30, 0.54, 0.78, 0.99)
        for frac in positions:
            idx = min(n, max(1, int(round(frac * (n - 1))) + 1))
            axis_x.append(times.iloc[idx - 1].strftime(label_fmt), float(idx))

        pad = (max(closes) - min(closes)) * 0.08 or (abs(last) * 0.005 or 1.0)
        axis_y = QtCharts.QValueAxis()
        axis_y.setRange(min(closes) - pad, max(closes) + pad)
        axis_y.setTickCount(5)
        axis_y.setLabelFormat("%.2f" if max(closes) < 1000 else "%.1f")
        axis_y.setLabelsColor(QtGui.QColor("#5f6b7a"))
        axis_y.setGridLineColor(QtGui.QColor("#eef1f6"))

        chart.addAxis(axis_x, QtCore.Qt.AlignBottom)
        chart.addAxis(axis_y, QtCore.Qt.AlignRight)

        line.attachAxis(axis_x)
        line.attachAxis(axis_y)
        self._tip_times = [t.strftime("%m-%d %H:%M") for t in times]
        self._tip_values = [float(c) for c in closes]
        line.hovered.connect(self._show_tip)

        # 首值基准虚线
        ref = QtCharts.QLineSeries()
        ref_pen = QtGui.QPen(QtGui.QColor("#b8c2d1"))
        ref_pen.setStyle(QtCore.Qt.DashLine)
        ref_pen.setWidthF(1.0)
        ref.setPen(ref_pen)
        ref.append(1.0, float(first))
        ref.append(float(n), float(first))
        chart.addSeries(ref)
        ref.attachAxis(axis_x)
        ref.attachAxis(axis_y)

        # 最新价圆点
        dot = QtCharts.QScatterSeries()
        dot.setMarkerSize(9.0)
        dot.setColor(QtGui.QColor(color))
        dot.setBorderColor(QtGui.QColor("#ffffff"))
        dot.append(float(n), float(last))
        chart.addSeries(dot)
        dot.attachAxis(axis_x)
        dot.attachAxis(axis_y)

        arrow = "▲" if up else "▼"
        self.title.setText(
            f"<b style='font-size:15px'>{self.code} {self.name}</b>　"
            f"<span style='color:{color};font-weight:700;font-size:15px'>"
            f"{last:.2f} {arrow} {change:+.2f}%</span>"
        )
        t0 = times.iloc[0].strftime("%m-%d %H:%M")
        t1 = times.iloc[-1].strftime("%m-%d %H:%M")
        is_crypto = self.market in ("加密", "加密货币")
        shown = label.replace("当日", "最近") if is_crypto else label
        if is_crypto:
            source_txt = f"Binance {crypto.pair_of(self.code)}（USDT）"
        else:
            source_txt = f"{chartdata.sina_symbol(self.code, self.market)}（新浪）"
        self.hint.setText(
            f"{shown}　区间 {t0} → {t1}　共 {len(closes)} 根 K 线　"
            f"最高 {max(closes):.2f}　最低 {min(closes):.2f}　"
            f"数据源 {source_txt}　"
            f"{datetime.now().strftime('%H:%M:%S')} 更新"
        )

    def _show_tip(self, point, state: bool) -> None:
        if not state:
            QtWidgets.QToolTip.hideText()
            return
        idx = int(round(point.x())) - 1
        times = getattr(self, "_tip_times", [])
        values = getattr(self, "_tip_values", [])
        if 0 <= idx < len(times) and 0 <= idx < len(values):
            QtWidgets.QToolTip.showText(
                QtGui.QCursor.pos(), f"{times[idx]}\n{values[idx]:.2f}"
            )
        else:
            QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"{point.y():.2f}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("多因子监控面板")
        self.resize(1180, 720)

        self.config = data_source.load_config()
        self.scored: pd.DataFrame = pd.DataFrame()
        self.filtered: pd.DataFrame = pd.DataFrame()
        self.data_source_tag = "codes"
        self.news_df: pd.DataFrame = pd.DataFrame()
        self.chart_win = None
        self._worker = None  # 当前正在跑的行情线程
        self._opt_worker = None  # 当前正在跑的期权线程

        self._build_ui()
        # 先让窗口显示出来，再拉行情（否则启动时几十秒没有反应）
        self.status.showMessage("正在加载行情数据，请稍候 …")
        QtCore.QTimer.singleShot(0, self._startup_load)

    def _startup_load(self) -> None:
        """窗口渲染完成后先贴上次快照，再后台刷新，启动不再卡十几秒。"""
        QtWidgets.QApplication.processEvents()
        shown = self._render_snapshot()
        if shown:
            self.status.showMessage("已显示上次缓存结果，正在后台刷新 …")
        self.refresh(fast=not shown)
        self.refresh_news()

    def _render_snapshot(self) -> bool:
        """把磁盘上的上次结果先画出来，成功返回 True。"""
        try:
            frame = data_source.load_snapshot(self.market_box.currentText())
        except Exception:
            frame = None
        if frame is None or frame.empty:
            return False
        self._apply_frame(frame, stale=True)
        return True

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # 顶部控制栏
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("市场："))
        self.market_box = QtWidgets.QComboBox()
        self.market_box.addItems(["A股", "美股", "加密货币"])
        bar.addWidget(self.market_box)

        bar.addSpacing(12)
        bar.addWidget(QtWidgets.QLabel("数据源："))
        self.source_box = QtWidgets.QComboBox()
        self.source_box.addItems(["自动", "实时行情", "仅代码表"])
        bar.addWidget(self.source_box)

        bar.addSpacing(12)
        bar.addWidget(QtWidgets.QLabel("最低得分："))
        self.score_spin = QtWidgets.QSpinBox()
        self.score_spin.setRange(0, 100)
        self.score_spin.setValue(0)
        bar.addWidget(self.score_spin)

        bar.addSpacing(12)
        bar.addWidget(QtWidgets.QLabel("关键字："))
        self.keyword = QtWidgets.QLineEdit()
        self.keyword.setPlaceholderText("代码 / 名称")
        self.keyword.setMaximumWidth(140)
        bar.addWidget(self.keyword)

        bar.addStretch(1)

        self.refresh_btn = QtWidgets.QPushButton("🔄 刷新")
        self.export_btn = QtWidgets.QPushButton("⬇️ 导出 CSV")
        bar.addWidget(self.refresh_btn)
        bar.addWidget(self.export_btn)
        root.addLayout(bar)

        # 评级筛选
        grade_row = QtWidgets.QHBoxLayout()
        grade_row.addWidget(QtWidgets.QLabel("评级："))
        self.grade_checks: dict[str, QtWidgets.QCheckBox] = {}
        for g in ["强势", "偏强", "中性", "偏弱"]:
            cb = QtWidgets.QCheckBox(g)
            cb.setChecked(True)
            self.grade_checks[g] = cb
            grade_row.addWidget(cb)
        grade_row.addStretch(1)
        root.addLayout(grade_row)

        # 选项卡：股票监控 / 期权观察
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs)

        self.table = QtWidgets.QTableWidget()
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignCenter)
        self.table.itemSelectionChanged.connect(self._on_row_selected)

        # 概览条：一屏看清强弱分布与数据来源
        self.overview = QtWidgets.QLabel("正在加载…")
        self.overview.setTextFormat(QtCore.Qt.RichText)
        self.overview.setWordWrap(True)
        self.overview.setStyleSheet(
            "QLabel{background:#f3f7fd;border:1px solid #dbe6f5;border-radius:6px;"
            "padding:6px 10px;font-size:13px;}"
        )

        # 选中行明细：原始指标 + 因子构成，避免反复切页
        self.detail = QtWidgets.QLabel("点击任意一行查看该标的明细；双击该行（或点右侧按钮）看实时折线图。")
        self.detail.setTextFormat(QtCore.Qt.RichText)
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(48)
        self.detail.setStyleSheet(
            "QLabel{background:#fbfcfe;border:1px solid #e3e8ef;border-radius:6px;"
            "padding:6px 10px;font-size:12px;}"
        )
        self.chart_btn = QtWidgets.QPushButton("📈 看折线图")
        self.chart_btn.setMinimumWidth(110)
        self.chart_btn.setToolTip("打开所选标的的分时 / 日线折线图（窗口内可设自动刷新）")

        detail_row = QtWidgets.QHBoxLayout()
        detail_row.setSpacing(6)
        detail_row.addWidget(self.detail, 1)
        detail_row.addWidget(self.chart_btn)

        factor_page = QtWidgets.QWidget()
        factor_layout = QtWidgets.QVBoxLayout(factor_page)
        factor_layout.setContentsMargins(0, 6, 0, 0)
        factor_layout.setSpacing(6)
        factor_layout.addWidget(self.overview)
        factor_layout.addWidget(self.table, 1)
        factor_layout.addLayout(detail_row)

        self.tabs.addTab(factor_page, "📊 因子监控")

        self._build_option_tab()
        self._build_news_tab()
        self._build_ai_tab()

        # 状态栏
        self.status = self.statusBar()
        self.status.showMessage("就绪")

        # 信号
        self.refresh_btn.clicked.connect(self.refresh)
        self.export_btn.clicked.connect(self.export_csv)
        self.chart_btn.clicked.connect(self._open_chart_for_selection)
        self.table.itemDoubleClicked.connect(lambda *_: self._open_chart_for_selection())
        self.market_box.currentTextChanged.connect(self.refresh)
        self.source_box.currentTextChanged.connect(self.refresh)
        self.score_spin.valueChanged.connect(self.apply_filter)
        self.keyword.textChanged.connect(self.apply_filter)
        for cb in self.grade_checks.values():
            cb.toggled.connect(self.apply_filter)

    def _build_option_tab(self) -> None:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("标的："))
        self.opt_market = QtWidgets.QComboBox()
        for m in options.list_markets():
            self.opt_market.addItem(m["name"], m["key"])
        self.opt_market.setMinimumWidth(190)
        self.opt_market.currentIndexChanged.connect(self.reload_option_expiries)
        row1.addWidget(self.opt_market)

        row1.addWidget(QtWidgets.QLabel("到期："))
        self.opt_expiry = QtWidgets.QComboBox()
        self.opt_expiry.setMinimumWidth(120)
        row1.addWidget(self.opt_expiry)

        self.opt_btn = QtWidgets.QPushButton("拉取真实期权链")
        self.opt_btn.clicked.connect(self.refresh_option_chain)
        row1.addWidget(self.opt_btn)

        self.opt_theory_btn = QtWidgets.QPushButton("BS 理论对照")
        self.opt_theory_btn.clicked.connect(self.refresh_option_theory)
        row1.addWidget(self.opt_theory_btn)
        row1.addStretch(1)
        layout.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("标的现价："))
        self.spot_edit = QtWidgets.QDoubleSpinBox()
        self.spot_edit.setRange(0.0, 100000.0)
        self.spot_edit.setDecimals(4)
        self.spot_edit.setValue(0.0)
        row2.addWidget(self.spot_edit)

        row2.addWidget(QtWidgets.QLabel("行权价间距："))
        self.step_edit = QtWidgets.QDoubleSpinBox()
        self.step_edit.setRange(0.01, 1000.0)
        self.step_edit.setValue(50.0)
        row2.addWidget(self.step_edit)

        row2.addWidget(QtWidgets.QLabel("上下档数："))
        self.around_edit = QtWidgets.QSpinBox()
        self.around_edit.setRange(1, 10)
        self.around_edit.setValue(3)
        row2.addWidget(self.around_edit)

        row2.addWidget(QtWidgets.QLabel("年化波动率%："))
        self.vol_edit = QtWidgets.QSpinBox()
        self.vol_edit.setRange(5, 200)
        self.vol_edit.setValue(20)
        row2.addWidget(self.vol_edit)

        self.opt_info = QtWidgets.QLabel("尚未拉取")
        row2.addWidget(self.opt_info)
        row2.addStretch(1)
        layout.addLayout(row2)

        self.opt_table = QtWidgets.QTableWidget()
        self.opt_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.opt_table)

        tip = QtWidgets.QLabel(
            "数据来源：新浪财经实时行情（中金所股指期权 / 上交所 ETF 期权），"
            "含真实买卖价、持仓量与由市场价反解的隐含波动率；期现差异会导致平价偏离非零。"
            "本面板仅为工具演示，不构成投资建议。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.tabs.addTab(page, "🎯 期权观察")
        self.reload_option_expiries()

    def _build_news_tab(self) -> None:
        news_cfg = self.config.get("news", {}) or {}
        state = uistate.get_section("news")

        default_sources = state.get("sources") or news_cfg.get("sources", ["eastmoney", "cls"])
        default_limit = int(state.get("limit", news_cfg.get("limit", 50)))
        # 自动刷新默认开启、默认每分钟一次；用户改动后由 ui_state.json 记住
        default_auto = bool(state.get("auto_refresh", True))
        default_interval = int(state.get("interval_minutes", 1))
        if default_interval < 1:
            default_interval = 1

        self.news_timer = QtCore.QTimer(self)
        self.news_timer.timeout.connect(self.refresh_news)

        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("来源："))
        self.news_source_checks: dict[str, QtWidgets.QCheckBox] = {}
        for key, label in [("eastmoney", "东方财富"), ("cls", "财联社")]:
            cb = QtWidgets.QCheckBox(label)
            cb.setChecked(key in default_sources)
            self.news_source_checks[key] = cb
            bar.addWidget(cb)

        bar.addSpacing(12)
        bar.addWidget(QtWidgets.QLabel("条数："))
        self.news_limit = QtWidgets.QSpinBox()
        self.news_limit.setRange(10, 100)
        self.news_limit.setValue(default_limit)
        bar.addWidget(self.news_limit)

        bar.addSpacing(12)
        self.news_auto = QtWidgets.QCheckBox("自动刷新")
        self.news_auto.setChecked(default_auto)
        self.news_auto.setToolTip("勾选状态会被记住，下次打开面板自动恢复")
        bar.addWidget(self.news_auto)
        bar.addWidget(QtWidgets.QLabel("间隔(分)："))
        self.news_interval = QtWidgets.QSpinBox()
        self.news_interval.setRange(1, 60)
        self.news_interval.setValue(default_interval)
        bar.addWidget(self.news_interval)

        bar.addStretch(1)
        self.news_btn = QtWidgets.QPushButton("🔄 刷新新闻")
        bar.addWidget(self.news_btn)
        layout.addLayout(bar)

        self.news_table = QtWidgets.QTableWidget()
        self.news_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.news_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.news_table.setAlternatingRowColors(True)
        self.news_table.doubleClicked.connect(self._open_news_link)
        layout.addWidget(self.news_table)

        tip = QtWidgets.QLabel(
            "提示：双击某条新闻可在浏览器打开原文；无链接的快讯暂不支持跳转。"
            "自动刷新开关、间隔、来源与条数都会被记住，下次打开面板自动恢复。"
            "新闻来自公开财经快讯聚合，仅作信息参考，不构成投资建议。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        self.tabs.addTab(page, "🌍 全球新闻")

        self.news_btn.clicked.connect(self.refresh_news)
        self.news_auto.toggled.connect(self._on_news_settings_changed)
        self.news_interval.valueChanged.connect(self._on_news_settings_changed)
        self.news_limit.valueChanged.connect(self._on_news_settings_changed)
        for cb in self.news_source_checks.values():
            cb.toggled.connect(self._on_news_settings_changed)
        self._toggle_news_timer()
        self._save_news_state()

    # ------------------------------------------------------------ 新闻
    def _on_news_settings_changed(self, *_args) -> None:
        """任一设置改动：重排定时器并立刻落盘。"""
        self._toggle_news_timer()
        self._save_news_state()

    def _save_news_state(self) -> None:
        uistate.update_section("news", {
            "sources": [k for k, cb in self.news_source_checks.items() if cb.isChecked()],
            "limit": self.news_limit.value(),
            "auto_refresh": self.news_auto.isChecked(),
            "interval_minutes": self.news_interval.value(),
        })

    def _toggle_news_timer(self) -> None:
        if self.news_auto.isChecked():
            self.news_timer.start(self.news_interval.value() * 60 * 1000)
        else:
            self.news_timer.stop()

    def refresh_news(self) -> None:
        sources = [k for k, cb in self.news_source_checks.items() if cb.isChecked()]
        rss = (self.config.get("news", {}) or {}).get("rss_feeds", []) or []
        self.news_btn.setEnabled(False)
        self.status.showMessage("正在加载新闻…")

        self._news_worker = NewsWorker(sources, rss, self.news_limit.value())
        self._news_worker.done.connect(self._on_news_loaded)
        self._news_worker.start()

    def _on_news_loaded(self, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            self.news_df = news.fetch_news([], [], self.news_limit.value())
        else:
            self.news_df = df
        self._fill_news_table()
        self.news_btn.setEnabled(True)
        self.status.showMessage(f"新闻已更新：{len(self.news_df)} 条")

    def _fill_news_table(self) -> None:
        df = self.news_df
        headers = ["时间", "来源", "标题", "摘要", "链接"]
        self.news_table.setColumnCount(len(headers))
        self.news_table.setHorizontalHeaderLabels(headers)
        self.news_table.setRowCount(len(df))

        for row_idx, (_, row) in enumerate(df.iterrows()):
            url = str(row.get("url", "") or "")
            cells = [
                str(row.get("time", "") or "—"),
                str(row.get("source", "")),
                str(row.get("title", "")),
                str(row.get("summary", "") or ""),
                "打开" if url else "—",
            ]
            for col_idx, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(
                    QtCore.Qt.AlignCenter if col_idx in (0, 1, 4)
                    else QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter
                )
                self.news_table.setItem(row_idx, col_idx, item)

        self.news_table.setColumnWidth(0, 150)
        self.news_table.setColumnWidth(1, 90)
        self.news_table.setColumnWidth(2, 320)
        self.news_table.setColumnWidth(3, 380)
        self.news_table.setColumnWidth(4, 60)
        self.news_table.horizontalHeader().setStretchLastSection(False)

    def _open_news_link(self, index: QtCore.QModelIndex) -> None:
        row = index.row()
        if row >= len(self.news_df):
            return
        url = str(self.news_df.iloc[row].get("url", "") or "")
        if url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

    # -------------------------------------------------------------- AI
    def _build_ai_tab(self) -> None:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)

        # API Key 行
        key_row = QtWidgets.QHBoxLayout()
        key_row.addWidget(QtWidgets.QLabel("DeepSeek API Key："))
        self.ai_key_edit = QtWidgets.QLineEdit()
        self.ai_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.ai_key_edit.setPlaceholderText(
            "粘一个 sk- 开头的 Key（platform.deepseek.com/api_keys 生成）；"
            "本次会话有效，也可写进 watchlist.yaml 的 deepseek.api_key"
        )
        saved_key = (self.config.get("deepseek", {}) or {}).get("api_key", "")
        if saved_key:
            self.ai_key_edit.setText(saved_key)
        key_row.addWidget(self.ai_key_edit)
        self.ai_test_btn = QtWidgets.QPushButton("测试连接")
        self.ai_test_btn.setToolTip("只发一次最小请求，确认 Key 是否可用")
        self.ai_test_btn.clicked.connect(self._ai_test_key)
        key_row.addWidget(self.ai_test_btn)
        self.ai_status = QtWidgets.QLabel("")
        key_row.addWidget(self.ai_status)
        layout.addLayout(key_row)

        # 快捷按钮行
        btn_row = QtWidgets.QHBoxLayout()
        self.ai_stock_btn = QtWidgets.QPushButton("📈 分析当前股票池")
        self.ai_news_btn = QtWidgets.QPushButton("📰 总结最新新闻")
        btn_row.addWidget(self.ai_stock_btn)
        btn_row.addWidget(self.ai_news_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 对话区
        self.ai_chat = QtWidgets.QTextBrowser()
        self.ai_chat.setReadOnly(True)
        layout.addWidget(self.ai_chat)

        # 输入行
        input_row = QtWidgets.QHBoxLayout()
        self.ai_input = QtWidgets.QLineEdit()
        self.ai_input.setPlaceholderText("向 AI 助手提问，回车发送…")
        self.ai_send_btn = QtWidgets.QPushButton("发送")
        input_row.addWidget(self.ai_input)
        input_row.addWidget(self.ai_send_btn)
        layout.addLayout(input_row)

        self.tabs.addTab(page, "🤖 AI 助手")

        self.ai_stock_btn.clicked.connect(self._ai_analyze_stocks)
        self.ai_news_btn.clicked.connect(self._ai_summarize_news)
        self.ai_send_btn.clicked.connect(self._ai_send_custom)
        self.ai_input.returnPressed.connect(self._ai_send_custom)

    def _ai_cfg(self) -> dict:
        return self.config.get("deepseek", {}) or {}

    def _ai_key(self) -> str:
        return self.ai_key_edit.text().strip()

    def _append_chat(self, role: str, text: str) -> None:
        label = "你" if role == "user" else "AI"
        color = "#1a73e8" if role == "user" else "#2e7d32"
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.ai_chat.append(
            f'<p style="color:{color};font-weight:bold;">{label}</p>'
            f'<div style="white-space:pre-wrap;">{safe}</div>'
        )

    def _send_ai(self, messages: list) -> None:
        self.ai_stock_btn.setEnabled(False)
        self.ai_news_btn.setEnabled(False)
        self.ai_send_btn.setEnabled(False)
        self.ai_status.setText("思考中…")
        self.ai_worker = AiWorker(messages, self._ai_cfg(), self._ai_key())
        self.ai_worker.done.connect(self._on_ai_done)
        self.ai_worker.start()

    def _on_ai_done(self, text: str, error: str) -> None:
        self.ai_stock_btn.setEnabled(True)
        self.ai_news_btn.setEnabled(True)
        self.ai_send_btn.setEnabled(True)
        if error:
            self.ai_status.setText("调用失败")
            self._append_chat("assistant", f"⚠️ {error}")
        else:
            self.ai_status.setText("")
            self._append_chat("assistant", text)

    def _ai_test_key(self) -> None:
        """只发一次最小请求，判断 Key 能不能用。"""
        key = self._ai_key()
        if not key:
            self.ai_status.setText("先填 Key")
            return
        self.ai_test_btn.setEnabled(False)
        self.ai_status.setText("测试中…")
        messages = [{"role": "user", "content": "ping，请只回复 ok"}]
        self.ai_worker = AiWorker(messages, self._ai_cfg(), key)
        self.ai_worker.done.connect(self._on_ai_test_done)
        self.ai_worker.start()

    def _on_ai_test_done(self, text: str, error: str) -> None:
        self.ai_test_btn.setEnabled(True)
        if error:
            self.ai_status.setText("Key 不可用")
            self._append_chat("assistant", f"⚠️ 测试失败：{error}")
            return
        model = ai_assistant.resolve_model(self._ai_cfg().get("model", ""))
        self.ai_status.setText(f"Key 可用 ✓（{model}）")
        self._append_chat("assistant", f"连接正常，模型 {model} 已返回响应。")

    def _ai_context_messages(self, question: str, include_stocks: bool) -> list:
        parts = []
        if include_stocks:
            parts.append(ai_assistant.build_stock_context(self.scored))
        parts.append(ai_assistant.build_news_context(self.news_df))
        user_content = "\n\n".join(parts) + f"\n\n请基于以上数据回答：{question}"
        return [
            {"role": "system", "content": ai_assistant.SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    def _ai_analyze_stocks(self) -> None:
        question = "请简要分析当前打分靠前的标的，分别指出主要优势与风险。"
        self._append_chat("user", question)
        self._send_ai(self._ai_context_messages(question, include_stocks=True))

    def _ai_summarize_news(self) -> None:
        question = "请总结这些最新财经快讯的要点，分点列出，并提示可能影响的板块。"
        self._append_chat("user", question)
        self._send_ai(self._ai_context_messages(question, include_stocks=False))

    def _ai_send_custom(self) -> None:
        text = self.ai_input.text().strip()
        if not text:
            return
        self.ai_input.clear()
        self._append_chat("user", text)
        self._send_ai(self._ai_context_messages(text, include_stocks=True))

    # -------------------------------------------------------------- 数据
    def _source_code(self) -> str:
        return {"自动": "auto", "实时行情": "live", "仅代码表": "codes"}[
            self.source_box.currentText()
        ]

    def refresh(self, fast: bool = False) -> None:
        """后台加载行情：主线程立即返回，结果回来后由回调填表。

        ``fast=True`` 只读 ROE 缓存（约 1 秒出表），``fast=False`` 允许回源补齐。
        """
        if getattr(self, "_worker", None) is not None:
            return  # 上一轮还没回来，忽略重复点击
        window = int(self.config.get("settings", {}).get("momentum_window", 20))
        kwargs = dict(
            market=self.market_box.currentText(),
            source=self._source_code(),
            window=window,
            watchlist=self.config.get("stocks", []),
            us_watchlist=self.config.get("us_stocks", []),
            pool_size=self.config.get("settings", {}).get("pool_size"),
            crypto_list=self.config.get("crypto", ["BTC"]),
            roe_mode="cached" if fast else "live",
        )
        self.refresh_btn.setEnabled(False)
        self.status.showMessage("正在后台刷新行情 …")
        worker = UniverseWorker(kwargs, "cached" if fast else "live")
        worker.done.connect(self._on_universe)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_universe(self, frame, phase: str, error: str) -> None:
        self._worker = None
        self.refresh_btn.setEnabled(True)
        if frame is None or getattr(frame, "empty", True):
            self.status.showMessage(
                f"加载失败：{error}" if error else "没取到行情数据，已保留当前结果"
            )
            return
        self._apply_frame(frame)
        if phase == "cached" and "roe" in self.scored and self.scored["roe"].isna().any():
            # 首屏已秒开，后台再补一轮完整数据（含 ROE），补完自动刷表
            QtCore.QTimer.singleShot(80, lambda: self.refresh(fast=False))

    def _apply_frame(self, frame, stale: bool = False) -> None:
        market = self.market_box.currentText()
        weights = self.config.get("weights", {})
        self.scored = factors.compute_factor_scores(frame, weights)
        self.data_source_tag = frame.attrs.get("source", "codes")
        tag = {
            "live": "实时行情",
            "crypto": "币安公开行情（USDT 计价）",
            "codes": "真实代码表（行情接口未取到，仅有代码与名称）",
        }.get(self.data_source_tag, self.data_source_tag)
        prefix = "上次缓存 · " if stale else ""
        self.status.showMessage(f"{prefix}数据来源：{tag}　|　监控标的：{len(self.scored)}")
        self.apply_filter()
        # 合约到期列表只跟市场有关，没必要每轮刷新都重新拉一遍
        if not stale and getattr(self, "_opt_market", None) != market:
            self._opt_market = market
            self.reload_option_expiries()

    def apply_filter(self) -> None:
        if self.scored.empty:
            return
        picked = [g for g, cb in self.grade_checks.items() if cb.isChecked()]
        self.filtered = screener.filter_universe(
            self.scored,
            min_score=self.score_spin.value(),
            grades=picked,
            code_query=self.keyword.text(),
        )
        self._fill_table()

    # -------------------------------------------------- 列定义（随市场切换）
    def _currency_label(self) -> str:
        """当前市场报价货币：A股是元，美股 USD，加密 USDT。"""
        market = self.market_box.currentText()
        if market == "美股":
            return "USD"
        if market == "加密货币":
            return "USDT"
        return "元"

    def _uses_cny_column(self) -> bool:
        """外币市场才补一列人民币折算。"""
        return self.market_box.currentText() in ("美股", "加密货币")

    def _fx_rate(self):
        try:
            return crypto.usdcny_rate()
        except Exception:
            return None

    def _build_columns(self):
        cols = list(DISPLAY_COLS)
        headers = list(HEADERS)
        tips = list(HEADER_TIPS)
        widths = dict(COL_WIDTHS)
        currency = self._currency_label()
        idx = cols.index("price")
        headers[idx] = f"最新价({currency})"
        tips[idx] = f"最新价，计价货币：{currency}"
        if self._uses_cny_column():
            cols.insert(idx + 1, "price_cny")
            headers.insert(idx + 1, "折人民币(¥)")
            tips.insert(idx + 1, "按美元兑人民币汇率折算，仅供参考")
            widths["price_cny"] = 112
        return cols, headers, tips, widths

    def _col_index(self, col: str) -> int:
        cols = getattr(self, "_cols", DISPLAY_COLS)
        return cols.index(col) if col in cols else -1

    def _with_cny(self, df: "pd.DataFrame"):
        if df is None or df.empty or not self._uses_cny_column():
            return df
        rate = self._fx_rate()
        out = df.copy()
        out["price_cny"] = [crypto.to_cny(p, rate) for p in out["price"]]
        return out

    def _fill_table(self) -> None:
        cols, headers, tips, widths = self._build_columns()
        self._cols = cols
        self._col_widths = widths
        df = self._with_cny(self.filtered)
        self.detail.setText("点击任意一行查看该标的明细；点击表头可按该列排序。")
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(df))
        for idx in range(min(len(tips), len(headers))):
            item = self.table.horizontalHeaderItem(idx)
            if item is not None:
                item.setToolTip(tips[idx])
        score_idx = cols.index("total_score")
        score_header = self.table.horizontalHeaderItem(score_idx)
        if score_header is not None:
            score_header.setToolTip(tips[score_idx] + self._weight_hint())

        for row_idx, (_, row) in enumerate(df.iterrows()):
            grade = str(row.get("grade", ""))
            for col_idx, col in enumerate(cols):
                val = row.get(col)
                text, sort_val = self._cell_text(val, col)
                item = NumericItem(text, sort_val)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self._style_item(item, col, val, grade)
                self.table.setItem(row_idx, col_idx, item)

        self._resize_columns()
        self.table.setSortingEnabled(True)
        self.table.setUpdatesEnabled(True)
        self._update_overview()

    # -------------------------------------------------- 因子监控展示细节
    def _cell_text(self, val, col: str):
        """返回（展示文本，排序值）。"""
        missing = val is None or (not isinstance(val, str) and pd.isna(val))
        if missing:
            return "—", None
        if col in ("rank",):
            return f"{int(val)}", int(val)
        if col in ("code", "name", "grade"):
            return str(val), None
        if col == "price":
            return f"{float(val):.2f}", float(val)
        if col == "price_cny":
            v = float(val)
            if v >= 100:
                return f"{v:,.0f}", v
            if v >= 1:
                return f"{v:,.2f}", v
            return f"{v:.4f}", v
        if col == "change_pct":
            return f"{float(val):+.2f}%", float(val)
        if col == "total_score":
            return f"{float(val):.1f}", float(val)
        if col in FACTOR_COLS:
            score = float(val)
            return f"{score:.0f} {score_bar(score)}", score
        return str(val), None

    def _style_item(self, item: QtWidgets.QTableWidgetItem, col: str,
                    val, grade: str) -> None:
        missing = val is None or (not isinstance(val, str) and pd.isna(val))
        if missing:
            item.setForeground(QtGui.QColor("#9aa4b2"))
            item.setToolTip("该指标本轮未取到数据（行情接口限流或字段缺失）")
            return

        if col == "change_pct":
            num = float(val)
            if num > 0:
                item.setForeground(QtGui.QColor("#c62828"))
            elif num < 0:
                item.setForeground(QtGui.QColor("#2e7d32"))
            else:
                item.setForeground(QtGui.QColor("#5f6b7a"))
            item.setToolTip(f"当日涨跌幅 {num:+.2f}%")
        elif col == "total_score":
            num = float(val)
            color = "#0f6b2f" if num >= 70 else ("#b26a00" if num < 50 else "#243447")
            item.setForeground(QtGui.QColor(color))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setToolTip(f"综合得分 {num:.1f}（满分 100）")
        elif col == "grade":
            text_color, bg = GRADE_BADGE.get(grade, ("#243447", "#ffffff"))
            item.setForeground(QtGui.QColor(text_color))
            item.setBackground(QtGui.QColor(bg))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setToolTip(f"评级：{grade}")
        elif col in FACTOR_COLS:
            score = float(val)
            color = "#0f6b2f" if score >= 70 else ("#8a6d00" if score >= 40 else "#a13333")
            item.setForeground(QtGui.QColor(color))
            item.setToolTip(f"{FACTOR_NAMES.get(col, col)}因子得分 {score:.1f}（0~100，越高越符合偏好）")
        elif col == "code":
            font = item.font()
            font.setFamily("Consolas")
            item.setFont(font)
        elif col == "price_cny":
            item.setForeground(QtGui.QColor("#243447"))
            rate = self._fx_rate()
            if rate:
                item.setToolTip(
                    f"折人民币 {float(val):,.2f} 元（1 美元 = {rate:.4f} 元）"
                )
            else:
                item.setToolTip("折人民币：暂未取到汇率")
        elif col == "name":
            font = item.font()
            font.setBold(True)
            item.setFont(font)

    def _weight_hint(self) -> str:
        """把当前因子权重拼成一行提示，挂到「综合分」表头上。"""
        weights = self.config.get("weights", {}) or {}
        names = {"valuation": "估值", "momentum": "动量", "quality": "质量",
                 "volatility": "波动", "liquidity": "流动性"}
        parts = []
        for key, label in names.items():
            try:
                parts.append(f"{label} {float(weights.get(key, 0)) * 100:.0f}%")
            except (TypeError, ValueError):
                continue
        return "\n当前权重：" + "／".join(parts) if parts else ""

    def _resize_columns(self) -> None:
        cols = getattr(self, "_cols", DISPLAY_COLS)
        widths = getattr(self, "_col_widths", COL_WIDTHS)
        for idx, col in enumerate(cols):
            if col in widths:
                self.table.setColumnWidth(idx, widths[col])
            else:
                self.table.setColumnWidth(idx, 112)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QtWidgets.QHeaderView.Interactive)
        header.setStretchLastSection(True)

    def _update_overview(self) -> None:
        df = self.scored
        if df is None or df.empty:
            self.overview.setText("暂无数据，点右上角「🔄 刷新」重新加载")
            return
        counts = df["grade"].value_counts().to_dict() if "grade" in df else {}
        total = len(df)
        shown = len(self.filtered) if self.filtered is not None else total
        avg = float(df["total_score"].mean()) if "total_score" in df else float("nan")
        badge_colors = {"强势": "#0f6b2f", "偏强": "#2f7d43",
                        "中性": "#8a6d00", "偏弱": "#a13333"}
        chunks = []
        for g in ("强势", "偏强", "中性", "偏弱"):
            n = int(counts.get(g, 0))
            if n == 0 and shown == total:
                continue
            chunks.append(
                f"<span style='color:{badge_colors[g]};font-weight:600'>● {g} {n}</span>"
            )
        src = {"live": "实时行情", "crypto": "币安行情",
               "codes": "仅代码表"}.get(self.data_source_tag, self.data_source_tag)
        unit = "个" if self.data_source_tag == "crypto" else "只"
        html = "　".join(chunks)
        html += (f"　<span style='color:#44536a'>｜ 平均分 <b>{avg:.1f}</b>"
                 f"　共 {total} {unit}</span>")
        if shown != total:
            html += f"　<span style='color:#44536a'>｜ 当前筛选 {shown} {unit}</span>"
        if self._uses_cny_column():
            rate = self._fx_rate()
            if rate:
                html += (f"　<span style='color:#44536a'>｜ 美元/人民币 "
                         f"<b>{rate:.4f}</b></span>")
            else:
                html += "　<span style='color:#9aa4b2'>｜ 汇率暂缺</span>"
        html += (f"　<span style='color:#8a94a6'>｜ 数据：{src}　"
                 f"{datetime.now().strftime('%H:%M:%S')}</span>")
        self.overview.setText(html)

    def _on_row_selected(self) -> None:
        sel = self.table.selectionModel()
        rows = sel.selectedRows() if sel is not None else []
        if not rows:
            return
        row_idx = rows[0].row()
        code_item = self.table.item(row_idx, self._col_index("code"))
        code = (code_item.text() if code_item is not None else "").strip()
        if not code or self.scored is None or self.scored.empty:
            return
        codes = self.scored["code"].astype(str).str.strip()
        match = self.scored[codes == code]
        if match.empty and code.isdigit():
            match = self.scored[codes.str.zfill(6) == code.zfill(6)]
        if match.empty:
            self.detail.setText("未在数据中找到该标的明细")
            return
        row = match.iloc[0]

        def fmt(key: str, digits: int = 2, suffix: str = "") -> str:
            v = row.get(key)
            if v is None or (not isinstance(v, str) and pd.isna(v)):
                return "<span style='color:#9aa4b2'>—</span>"
            try:
                return f"{float(v):.{digits}f}{suffix}"
            except (TypeError, ValueError):
                return f"{v}{suffix}"

        name = row.get("name", "")
        grade = str(row.get("grade", ""))
        text_color, _bg = GRADE_BADGE.get(grade, ("#243447", "#ffffff"))
        price_unit = self._currency_label()
        cny_part = ""
        if self._uses_cny_column():
            cny_val = crypto.to_cny(row.get("price"), self._fx_rate())
            if cny_val == cny_val:
                cny_part = f"　≈ <b>¥{cny_val:,.0f}</b>"
        factors = "　".join(
            f"<span style='color:#556'>{label} "
            f"<b>{fmt(col, 0)}</b> "
            f"<span style='color:#2f7d43'>{score_bar(row.get(col))}</span></span>"
            for col, label in zip(FACTOR_COLS, ["估值", "动量", "质量", "波动", "流动性"])
        )
        self.detail.setText(
            f"<b>{code} {name}</b>　"
            f"<span style='color:{text_color};font-weight:600'>[{grade}]</span>　"
            f"最新 <b>{fmt('price')}</b> {price_unit}{cny_part}　"
            f"涨跌 {fmt('change_pct', 2, '%')}　"
            f"PE {fmt('pe', 1)}　PB {fmt('pb', 2)}　ROE {fmt('roe', 1, '%')}　"
            f"换手 {fmt('turnover', 2, '%')}　量比 {fmt('volume_ratio', 2)}　"
            f"成交额 {fmt('amount', 2, ' 亿')}　20日涨跌 {fmt('momentum_20d', 2, '%')}　"
            f"20日波动 {fmt('volatility_20d', 2, '%')}<br>"
            f"<span style='color:#44536a'>综合分 <b>{fmt('total_score', 1)}</b>　｜　{factors}</span>"
        )

        # 折线图窗口已打开时跟随选股自动切换
        if self.chart_win is not None and self.chart_win.isVisible():
            try:
                self.chart_win.follow(code, str(name), self.market_box.currentText())
            except Exception:
                pass

    def _open_chart_for_selection(self) -> None:
        sel = self.table.selectionModel()
        rows = sel.selectedRows() if sel is not None else []
        if not rows:
            QtWidgets.QMessageBox.information(self, "提示", "先在表格里点选一只股票。")
            return
        row_idx = rows[0].row()
        code_item = self.table.item(row_idx, self._col_index("code"))
        name_item = self.table.item(row_idx, self._col_index("name"))
        if code_item is None:
            return
        code = code_item.text().strip()
        name = name_item.text().strip() if name_item is not None else code
        if self.chart_win is None:
            self.chart_win = ChartWindow(self)
        self.chart_win.show_for(code, name, self.market_box.currentText())

    @staticmethod
    def _fmt(val, col: str) -> str:
        if pd.isna(val):
            return "—"
        if col in ("price",):
            return f"{float(val):.2f}"
        if col == "change_pct":
            return f"{float(val):+.2f}"
        if col in ("total_score", "valuation_score", "momentum_score",
                   "quality_score", "volatility_score", "liquidity_score"):
            return f"{float(val):.1f}"
        return str(val)

    # -------------------------------------------------------------- 期权
    def _current_market(self) -> str:
        return self.opt_market.currentData() or "io"

    def reload_option_expiries(self) -> None:
        market = self._current_market()
        self.opt_expiry.blockSignals(True)
        self.opt_expiry.clear()
        self.opt_expiry.blockSignals(False)
        try:
            seasons = options.list_expiries(market)
        except Exception:
            seasons = []
        if seasons:
            self.opt_expiry.addItems(seasons)
            default = options._default_expiry(market, seasons)
            if default:
                self.opt_expiry.setCurrentText(default)
            self.opt_info.setText(f"可选合约 {len(seasons)} 个，点「拉取真实期权链」")
        else:
            self.opt_info.setText("合约列表获取失败，请检查网络后重试")

    def refresh_option_chain(self) -> None:
        if getattr(self, "_opt_worker", None) is not None:
            return
        market = self._current_market()
        expiry = self.opt_expiry.currentText().strip()
        self.status.showMessage("正在后台拉取期权行情…")
        self.opt_info.setText("正在拉取期权链，界面可继续操作 …")
        worker = OptionWorker(market, expiry)
        worker.done.connect(self._on_option_chain)
        worker.finished.connect(worker.deleteLater)
        self._opt_worker = worker
        worker.start()

    def _on_option_chain(self, chain, info, error: str) -> None:
        self._opt_worker = None
        if error:
            QtWidgets.QMessageBox.warning(self, "期权拉取失败", f"行情接口出错：{error}")
            self.status.showMessage("期权行情拉取失败")
            return
        if chain is None or getattr(chain, "empty", True):
            self.opt_info.setText("该合约暂无行情数据")
            self.opt_table.setRowCount(0)
            self.status.showMessage("该合约暂无行情数据")
            return
        spot = info.get("spot")
        if spot:
            self.spot_edit.setValue(float(spot))
        self.opt_info.setText(
            f"{info['market']}｜{info['expiry']} 到期 {info['expiry_date']}（剩 {info['days_left']} 天）"
            f"｜现价 {spot if spot else '—'}｜{info['fetched_at']}"
        )
        self._fill_option_table(chain)
        self.status.showMessage(
            f"期权行情已更新：{len(chain)} 档行权价（{info['source']}）"
        )

    def refresh_option_theory(self) -> None:
        spot = self.spot_edit.value()
        if spot <= 0:
            QtWidgets.QMessageBox.information(
                self, "提示", "请先拉取真实期权链，或手动填入标的现价。"
            )
            return
        market = self._current_market()
        expiry = self.opt_expiry.currentText().strip()
        T = 30.0 / 365.0
        if expiry:
            try:
                T = max(options.expiry_info(market, expiry)[2], 1.0 / 365.0)
            except Exception:
                pass
        chain = options.build_option_chain(
            spot=spot,
            strike_step=self.step_edit.value(),
            strikes_around=self.around_edit.value(),
            T=T,
            r=0.02,
            sigma=self.vol_edit.value() / 100.0,
        )
        self._fill_option_table(chain)
        self.status.showMessage("已生成 Black-Scholes 理论对照表")

    def _fill_option_table(self, chain) -> None:
        self.opt_table.setColumnCount(len(chain.columns))
        self.opt_table.setHorizontalHeaderLabels([str(c) for c in chain.columns])
        self.opt_table.setRowCount(len(chain))
        for r_idx, (_, row) in enumerate(chain.iterrows()):
            for c_idx, col in enumerate(chain.columns):
                v = row[col]
                if isinstance(v, str):
                    text = v
                elif pd.isna(v):
                    text = "—"
                else:
                    fv = float(v)
                    name = str(col)
                    if "波动率" in name:
                        text = f"{fv * 100:.1f}%"
                    elif "持仓量" in name:
                        text = f"{fv:.0f}"
                    elif abs(fv) >= 100:
                        text = f"{fv:.2f}"
                    elif abs(fv) >= 10:
                        text = f"{fv:.1f}"
                    elif abs(fv) >= 1:
                        text = f"{fv:.3f}"
                    else:
                        text = f"{fv:.4f}"
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.opt_table.setItem(r_idx, c_idx, item)
        self.opt_table.resizeColumnsToContents()

    # -------------------------------------------------------------- 导出
    def export_csv(self) -> None:
        if self.filtered.empty:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有可导出的数据。")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "导出 CSV", "factor_screen.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write(screener.to_csv(self.filtered))
            self.status.showMessage(f"已导出：{path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(exc))


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName("多因子监控面板")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
