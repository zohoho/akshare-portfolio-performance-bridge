from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from app_paths import (
    APP_DISPLAY_NAME,
    BRIDGE_VERSION,
    SERVICE_HOST,
    SERVICE_PORT,
    cache_dir,
    data_dir,
    gui_log_file,
    install_dir,
    logs_dir,
    read_json,
    runtime_state_file,
    update_status_file,
)
from service_manager import (
    get_health,
    open_path,
    restart_service,
    set_startup_enabled,
    start_service,
    startup_enabled,
    stop_service,
)
from update_manager import check_latest_version, install_version, rollback


logging.basicConfig(
    filename=gui_log_file(),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


class BridgeApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_DISPLAY_NAME)
        self.geometry("900x680")
        self.minsize(780, 600)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self._configure_scaling()
        self._configure_styles()

        self.status_var = tk.StringVar(value="正在检测…")
        self.bridge_version_var = tk.StringVar(value=BRIDGE_VERSION)
        self.akshare_version_var = tk.StringVar(value="—")
        self.latest_version_var = tk.StringVar(value="—")
        self.checked_at_var = tk.StringVar(value="—")
        self.started_at_var = tk.StringVar(value="—")
        self.startup_var = tk.BooleanVar(value=False)
        self.update_banner_var = tk.StringVar(value="")
        self.maintenance_var = tk.StringVar(value="就绪")

        self._build_header()
        self._build_tabs()
        self._build_footer()
        self.after(100, self.refresh_status)
        self.after(600, lambda: self.run_background(self._initial_update_check, "检查更新"))

    def _configure_scaling(self) -> None:
        try:
            dpi = self.winfo_fpixels("1i")
            self.tk.call("tk", "scaling", max(1.0, dpi / 72.0))
        except tk.TclError:
            pass

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Subtitle.TLabel", foreground="#5B6573")
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Banner.TLabel", foreground="#8A5200", background="#FFF4D8", padding=10)
        style.configure("Value.TLabel", font=("Microsoft YaHei UI", 10, "bold"))

    def _build_header(self) -> None:
        frame = ttk.Frame(self, padding=(22, 18, 22, 10))
        frame.pack(fill="x")
        ttk.Label(frame, text="AkShare 报价桥接器", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text="为 Portfolio Performance 提供中国、香港与美国市场报价",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.status_label = ttk.Label(frame, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.grid(row=0, column=1, rowspan=2, sticky="e")
        frame.columnconfigure(0, weight=1)

        self.update_banner = ttk.Label(self, textvariable=self.update_banner_var, style="Banner.TLabel")

    def _build_tabs(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        self.overview_tab = ttk.Frame(notebook, padding=18)
        self.test_tab = ttk.Frame(notebook, padding=18)
        self.maintenance_tab = ttk.Frame(notebook, padding=18)
        notebook.add(self.overview_tab, text="概览")
        notebook.add(self.test_tab, text="数据测试")
        notebook.add(self.maintenance_tab, text="维护")
        self._build_overview()
        self._build_test_tab()
        self._build_maintenance()

    def _build_overview(self) -> None:
        details = ttk.LabelFrame(self.overview_tab, text="运行信息", style="Section.TLabelframe", padding=10)
        details.pack(fill="x")
        def add_value(row: int, label_column: int, label: str, value: Any, value_column: int) -> None:
            ttk.Label(details, text=label, width=14).grid(row=row, column=label_column, sticky="w", padx=(0, 6), pady=2)
            widget = ttk.Label(details, textvariable=value, style="Value.TLabel") if isinstance(value, tk.StringVar) else ttk.Label(details, text=value, style="Value.TLabel")
            widget.grid(row=row, column=value_column, columnspan=1, sticky="w", padx=(0, 20), pady=2)

        add_value(0, 0, "服务地址", f"http://{SERVICE_HOST}:{SERVICE_PORT}", 1)
        details.grid_columnconfigure(1, weight=1)
        details.grid_columnconfigure(3, weight=1)
        add_value(1, 0, "桥接器版本", self.bridge_version_var, 1)
        add_value(1, 2, "运行中 AkShare", self.akshare_version_var, 3)
        add_value(2, 0, "PyPI 最新版本", self.latest_version_var, 1)
        add_value(2, 2, "上次检查更新", self.checked_at_var, 3)
        add_value(3, 0, "服务启动时间", self.started_at_var, 1)

        controls = ttk.LabelFrame(self.overview_tab, text="服务控制", style="Section.TLabelframe", padding=16)
        controls.pack(fill="x", pady=(10, 0))
        ttk.Button(controls, text="启动服务", command=lambda: self.run_background(start_service, "启动服务")).pack(side="left")
        ttk.Button(controls, text="暂停服务", command=lambda: self.run_background(stop_service, "暂停服务")).pack(side="left", padx=8)
        ttk.Button(controls, text="重启服务", command=lambda: self.run_background(restart_service, "重启服务")).pack(side="left")
        ttk.Button(controls, text="结束后台服务", command=self.confirm_end_service).pack(side="left", padx=8)
        ttk.Button(controls, text="刷新状态", command=self.refresh_status).pack(side="right")

        hint = ttk.Label(
            self.overview_tab,
            text="暂停服务：只停止当前报价进程，登录自启仍保留。结束后台服务：停止服务并关闭登录自启。关闭本窗口不会停止服务。",
            style="Subtitle.TLabel",
            wraplength=760,
        )
        hint.pack(anchor="w", pady=(14, 0))

    def _build_test_tab(self) -> None:
        instructions = ttk.LabelFrame(self.test_tab, text="Portfolio Performance 填写说明", style="Section.TLabelframe", padding=12)
        instructions.pack(fill="x")
        ttk.Label(
            instructions,
            text=(
                "1. 国内基金选择“开放式基金”或“场内 ETF”，A 股选择“A 股”。\n"
                "2. 港股可输入 0700 或 0700.HK；港基可输入 ISIN（如 HK0000294535）或天天基金内部代码；美股/美基输入 MSFT、SPY 等代码。\n"
                "3. PP 的历史报价提供方选 JSON；日期路径填 $.prices[*].date，收盘价路径填 $.prices[*].close，日期格式留空。\n"
                "4. 基金的“最新报价”选择“与历史报价相同”；股票使用配置中给出的最新报价 URL。以后新增同类资产只需复制证券并修改名称、代码。"
            ),
            justify="left",
            wraplength=760,
        ).pack(anchor="w")

        entry_frame = ttk.LabelFrame(self.test_tab, text="测试报价", style="Section.TLabelframe", padding=16)
        entry_frame.pack(fill="x", pady=(12, 0))
        ttk.Label(entry_frame, text="资产类型").grid(row=0, column=0, sticky="w")
        self.asset_type = ttk.Combobox(
            entry_frame,
            state="readonly",
            values=("开放式基金", "场内 ETF", "A 股", "港股", "美股", "港基", "美基"),
            width=18,
        )
        self.asset_type.set("开放式基金")
        self.asset_type.grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(entry_frame, text="基金/证券代码").grid(row=0, column=1, sticky="w", padx=(14, 0))
        self.ticker_entry = ttk.Entry(entry_frame, width=26)
        self.ticker_entry.insert(0, "000305")
        self.ticker_entry.grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=(5, 0))
        ttk.Button(entry_frame, text="获取最新报价", command=self.test_quote).grid(row=1, column=2, padx=(14, 0), pady=(5, 0))
        entry_frame.columnconfigure(1, weight=1)

        result_frame = ttk.LabelFrame(self.test_tab, text="测试结果", style="Section.TLabelframe", padding=16)
        result_frame.pack(fill="both", expand=True, pady=(16, 0))
        self.result_text = tk.Text(result_frame, height=11, wrap="word", relief="flat", background="#F7F8FA")
        self.result_text.pack(fill="both", expand=True)
        self.result_text.configure(state="disabled")
        button_row = ttk.Frame(result_frame)
        button_row.pack(fill="x", pady=(12, 0))
        ttk.Button(button_row, text="复制 Portfolio Performance 配置", command=self.copy_pp_config).pack(side="left")
        ttk.Button(button_row, text="复制原始 JSON 地址", command=self.copy_test_url).pack(side="left", padx=8)
        self._last_test_url = ""
        self._last_pp_config = ""

    def _build_maintenance(self) -> None:
        update_frame = ttk.LabelFrame(self.maintenance_tab, text="AkShare 更新", style="Section.TLabelframe", padding=16)
        update_frame.pack(fill="x")
        ttk.Label(update_frame, textvariable=self.maintenance_var, wraplength=680).pack(anchor="w")
        row = ttk.Frame(update_frame)
        row.pack(fill="x", pady=(12, 0))
        ttk.Button(row, text="立即检查", command=lambda: self.run_background(lambda: check_latest_version(force=True), "检查更新")).pack(side="left")
        ttk.Button(row, text="安装最新版本", command=self.confirm_install_update).pack(side="left", padx=8)
        ttk.Button(row, text="回滚上一版本", command=self.confirm_rollback).pack(side="left")

        startup_frame = ttk.LabelFrame(self.maintenance_tab, text="Windows 登录启动", style="Section.TLabelframe", padding=16)
        startup_frame.pack(fill="x", pady=(16, 0))
        ttk.Checkbutton(
            startup_frame,
            text="登录 Windows 后自动启动后台报价服务",
            variable=self.startup_var,
            command=self.toggle_startup,
        ).pack(anchor="w")

        files_frame = ttk.LabelFrame(self.maintenance_tab, text="文件与诊断", style="Section.TLabelframe", padding=16)
        files_frame.pack(fill="x", pady=(16, 0))
        ttk.Button(files_frame, text="打开日志目录", command=lambda: open_path(logs_dir())).pack(side="left")
        ttk.Button(files_frame, text="打开数据目录", command=lambda: open_path(data_dir())).pack(side="left", padx=8)
        ttk.Button(files_frame, text="打开缓存目录", command=lambda: open_path(cache_dir())).pack(side="left")
        ttk.Button(files_frame, text="查看 GPL-3.0 许可证", command=lambda: open_path(install_dir() / "LICENSE")).pack(side="left", padx=8)

        help_frame = ttk.LabelFrame(self.maintenance_tab, text="操作说明", style="Section.TLabelframe", padding=12)
        help_frame.pack(fill="x", pady=(16, 0))
        ttk.Label(
            help_frame,
            text=(
                "“暂停服务”用于临时停止本机报价接口，Windows 登录自启设置不变；“结束后台服务”会同时停止服务并取消登录自启。\n"
                "重新需要报价时点击“启动服务”；需要恢复自动启动时，在本页重新勾选“登录 Windows 后自动启动后台报价服务”。\n"
                "本程序按 GPL-3.0 发布，不提供任何担保；完整条款可通过上方按钮查看。"
            ),
            justify="left",
            wraplength=760,
        ).pack(anchor="w")

    def _build_footer(self) -> None:
        ttk.Separator(self).pack(fill="x")
        footer = ttk.Frame(self, padding=(18, 8))
        footer.pack(fill="x")
        ttk.Label(footer, text="仅供个人投资记账；报价不应用于交易决策。", style="Subtitle.TLabel").pack(side="left")
        ttk.Label(footer, text=f"GPL-3.0 · 无担保 · 桥接器 {BRIDGE_VERSION}", style="Subtitle.TLabel").pack(side="right")

    def run_background(self, action: Callable[[], Any], title: str, on_done: Callable[[Any], None] | None = None) -> None:
        self.maintenance_var.set(f"{title}处理中…")

        def worker() -> None:
            try:
                result = action()
                self.after(0, lambda: self._background_done(title, result, on_done))
            except Exception as exc:
                logging.exception("%s failed", title)
                detail = str(exc)
                self.after(0, lambda detail=detail: self._background_failed(title, detail))

        threading.Thread(target=worker, daemon=True).start()

    def _background_done(self, title: str, result: Any, on_done: Callable[[Any], None] | None) -> None:
        self.maintenance_var.set(f"{title}完成")
        if on_done:
            on_done(result)
        self.refresh_status()

    def _background_failed(self, title: str, detail: str) -> None:
        self.maintenance_var.set(f"{title}失败：{detail}")
        messagebox.showerror(title, detail, parent=self)
        self.refresh_status()

    def refresh_status(self) -> None:
        health = get_health()
        if health:
            self.status_var.set("● 服务运行中")
            self.status_label.configure(foreground="#16794A")
            self.bridge_version_var.set(str(health.get("bridge_version", "旧版服务")))
            self.akshare_version_var.set(str(health.get("akshare_version", "未知")))
            self.started_at_var.set(self.format_time(health.get("started_at")))
        else:
            self.status_var.set("● 服务已停止")
            self.status_label.configure(foreground="#A13D32")
            self.started_at_var.set("—")

        update = read_json(update_status_file(), {}) or {}
        self.latest_version_var.set(str(update.get("latest_version") or "尚未检查"))
        self.checked_at_var.set(self.format_time(update.get("checked_at")))
        current = health.get("akshare_version") if health else update.get("current_version")
        latest = update.get("latest_version")
        if update.get("update_available") and latest:
            self.update_banner_var.set(f"发现 AkShare 新版本 {latest}（当前 {current}）。请在“维护”页面确认安装。")
            if not self.update_banner.winfo_ismapped():
                self.update_banner.pack(fill="x", padx=18, pady=(0, 10), before=self.winfo_children()[2])
        else:
            self.update_banner_var.set("")
            if self.update_banner.winfo_ismapped():
                self.update_banner.pack_forget()
        try:
            self.startup_var.set(startup_enabled())
        except Exception:
            pass

    @staticmethod
    def format_time(value: Any) -> str:
        if not value:
            return "—"
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo:
                parsed = parsed.astimezone()
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return str(value)

    def _initial_update_check(self) -> dict[str, Any]:
        return check_latest_version(force=False)

    def _get_json(self, path: str, timeout: int = 90) -> dict[str, Any]:
        with urllib.request.urlopen(f"http://{SERVICE_HOST}:{SERVICE_PORT}{path}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_quote(self) -> None:
        ticker = self.ticker_entry.get().strip().upper()
        asset_type = self.asset_type.get()
        if not ticker:
            messagebox.showwarning("数据测试", "请输入基金或证券代码", parent=self)
            return
        if asset_type == "开放式基金":
            path = f"/fund/{ticker}/latest.json?kind=open&refresh=1"
            history_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/{{TICKER}}.json?kind=open"
            latest_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/{{TICKER}}/latest.json?kind=open"
            config = self.pp_config(history_url, latest_url, include_ohlcv=False, latest_same=True)
        elif asset_type == "场内 ETF":
            path = f"/fund/{ticker}/latest.json?kind=etf&refresh=1"
            history_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/{{TICKER}}.json?kind=etf"
            latest_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/{{TICKER}}/latest.json?kind=etf"
            config = self.pp_config(history_url, latest_url, include_ohlcv=False, latest_same=True)
        elif asset_type == "A 股":
            path = f"/stock/{ticker}/latest.json?refresh=1"
            history_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/stock/{{TICKER}}.json"
            latest_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/stock/{{TICKER}}/latest.json"
            config = self.pp_config(history_url, latest_url, include_ohlcv=True)
        elif asset_type == "港股":
            path = f"/stock/hk/{ticker}/latest.json?refresh=1"
            history_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/stock/hk/{{TICKER}}.json"
            latest_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/stock/hk/{{TICKER}}/latest.json"
            config = self.pp_config(history_url, latest_url, include_ohlcv=True)
        elif asset_type == "美股":
            path = f"/stock/us/{ticker}/latest.json?refresh=1"
            history_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/stock/us/{{TICKER}}.json"
            latest_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/stock/us/{{TICKER}}/latest.json"
            config = self.pp_config(history_url, latest_url, include_ohlcv=True)
        elif asset_type == "港基":
            path = f"/fund/hk/{ticker}/latest.json?refresh=1"
            history_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/hk/{{TICKER}}.json"
            latest_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/hk/{{TICKER}}/latest.json"
            config = self.pp_config(history_url, latest_url, include_ohlcv=False, latest_same=True)
        else:
            path = f"/fund/us/{ticker}/latest.json?refresh=1"
            history_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/us/{{TICKER}}.json"
            latest_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}/fund/us/{{TICKER}}/latest.json"
            config = self.pp_config(history_url, latest_url, include_ohlcv=False, latest_same=True)
        self._last_test_url = f"http://{SERVICE_HOST}:{SERVICE_PORT}{path}"
        self._last_pp_config = config

        def action() -> dict[str, Any]:
            if not get_health():
                start_service()
            return self._get_json(path)

        self.run_background(action, "获取报价", self.show_quote_result)

    def show_quote_result(self, payload: dict[str, Any]) -> None:
        prices = payload.get("prices") or []
        latest = prices[-1] if prices else {}
        lines = [
            f"名称：{payload.get('name') or '—'}",
            f"代码：{payload.get('ticker') or payload.get('code') or '—'}",
            f"日期：{latest.get('date') or '—'}",
            f"价格：{latest.get('close') or '—'} {payload.get('currency') or ''}",
            f"来源：{payload.get('source') or '—'}",
            f"拉取时间：{self.format_time(payload.get('fetched_at'))}",
            f"缓存状态：{'旧缓存（上游暂时不可用）' if payload.get('stale') else '最新数据'}",
            "",
            "Portfolio Performance 配置（可直接复制）：",
            self._last_pp_config,
        ]
        self.result_text.configure(state="normal")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", "\n".join(lines))
        self.result_text.configure(state="disabled")

    @staticmethod
    def pp_config(
        history_url: str,
        latest_url: str,
        *,
        include_ohlcv: bool,
        latest_same: bool = False,
    ) -> str:
        lines = [
            "历史报价提供方：JSON",
            f"历史报价 URL：{history_url}",
            "日期路径：$.prices[*].date",
            "收盘价路径：$.prices[*].close",
            "日期格式：留空",
            "报价系数：1",
        ]
        if latest_same:
            lines.append("最新报价：与历史报价相同")
        else:
            lines.append(f"最新报价 URL：{latest_url}")
        if include_ohlcv:
            lines.extend(
                [
                    "最高价路径：$.prices[*].high",
                    "最低价路径：$.prices[*].low",
                    "成交量路径：$.prices[*].volume",
                ]
            )
        return "\n".join(lines)

    def copy_pp_config(self) -> None:
        if not self._last_pp_config:
            messagebox.showinfo("复制配置", "请先完成一次数据测试", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_pp_config)
        self.update()
        self.maintenance_var.set("Portfolio Performance 配置已复制")

    def copy_test_url(self) -> None:
        if not self._last_test_url:
            messagebox.showinfo("复制地址", "请先完成一次数据测试", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(self._last_test_url)
        self.update()
        self.maintenance_var.set("测试 URL 已复制")

    def toggle_startup(self) -> None:
        desired = self.startup_var.get()
        self.run_background(lambda: set_startup_enabled(desired), "更新开机启动")

    def confirm_end_service(self) -> None:
        if not messagebox.askyesno(
            "结束后台服务",
            "这会停止当前报价服务，并关闭 Windows 登录自动启动。\n\n确定继续吗？",
            parent=self,
        ):
            return

        def end_service() -> bool:
            stopped = stop_service()
            set_startup_enabled(False)
            return stopped

        self.run_background(end_service, "结束后台服务")

    def confirm_install_update(self) -> None:
        status = read_json(update_status_file(), {}) or {}
        latest = status.get("latest_version")
        current = status.get("current_version") or self.akshare_version_var.get()
        if not latest:
            messagebox.showinfo("安装更新", "请先检查更新", parent=self)
            return
        if not status.get("update_available"):
            messagebox.showinfo("安装更新", f"当前 AkShare {current} 已是最新稳定版。", parent=self)
            return
        if messagebox.askyesno(
            "安装 AkShare 更新",
            f"将从 AkShare {current} 更新到 {latest}。\n\n程序会先在隔离目录安装并验证基金、股票接口，验证通过后才切换；失败时自动保留当前版本。",
            parent=self,
        ):
            self.run_background(lambda: install_version(str(latest), self.set_maintenance_safe), "安装 AkShare 更新")

    def confirm_rollback(self) -> None:
        state = read_json(runtime_state_file(), {}) or {}
        previous = state.get("previous")
        if not isinstance(previous, dict):
            messagebox.showinfo("回滚", "目前没有可回滚的上一版本。", parent=self)
            return
        if messagebox.askyesno("回滚 AkShare", f"确认回滚到 {previous.get('version', 'bundled')}？", parent=self):
            self.run_background(lambda: rollback(self.set_maintenance_safe), "回滚 AkShare")

    def set_maintenance_safe(self, message: str) -> None:
        self.after(0, lambda: self.maintenance_var.set(message))


def main() -> None:
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass
    BridgeApp().mainloop()
