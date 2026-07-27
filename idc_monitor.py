#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
核云IDC 服务器自动监测重启程序
功能: 定时检测服务器状态，发现关机自动开机，推送飞书交互式卡片通知
API 文档来源: https://git.masonliu.com/MasonLiu/VPSHUB
"""

from __future__ import annotations

import os
import sys
import time
import json
import logging
import signal
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("请先安装依赖: pip install requests")
    sys.exit(1)

# ============================================================
# 配置区 — 请修改为你的实际值
# ============================================================
CONFIG = {
    "api_base_url": "https://www.heyunidc.cn/v1",
    "api_account": "",        # 你的手机号或邮箱
    "api_password": "",       # 你的 API Key（在控制台生成）
    "check_interval": 300,    # 检测间隔（秒），默认 5 分钟
    "daily_reboot_limit": 3,  # 单台服务器每日最大自动重启次数
    "log_dir": "logs",
    "webhook_url": "",        # 飞书机器人 webhook URL
    "idc_console_url": "https://www.heyunidc.cn/console/vps",
    "repo_url": "",           # 可选：GitHub 仓库地址，卡片按钮会用到
}

# ============================================================
# 日志配置
# ============================================================
log_dir = Path(CONFIG["log_dir"])
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            log_dir / f"monitor_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger("idc-monitor")


class IDCMonitor:
    """核云IDC 监控器"""

    def __init__(self, config):
        self.config = config
        self.base_url = config["api_base_url"]
        self.jwt_token = None
        self.reboot_counts = {}   # {host_id: count} 今日已重启次数
        self.running = True

        # 注册优雅退出
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("收到退出信号，正在停止...")
        self.running = False

    # --------------------------------------------------------
    # 认证
    # --------------------------------------------------------
    def login(self) -> bool:
        """登录获取 JWT Token"""
        url = f"{self.base_url}/login_api"
        params = {
            "account": self.config["api_account"],
            "password": self.config["api_password"],
        }
        try:
            resp = requests.post(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # API 返回的 token 字段可能是 "token" 或 "jwt"
            token = data.get("token") or data.get("jwt")
            if token:
                self.jwt_token = token
                logger.info("登录成功，Token 已获取")
                return True
            else:
                logger.error(f"登录失败: {data}")
                return False
        except Exception as e:
            logger.error(f"登录请求异常: {e}")
            return False

    # --------------------------------------------------------
    # HTTP 请求封装
    # --------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs) -> dict | None:
        """带 Token 的统一请求方法"""
        if not self.jwt_token:
            logger.error("未登录，无法请求")
            return None

        headers = {"Authorization": f"JWT {self.jwt_token}"}
        url = f"{self.base_url}{path}"
        try:
            resp = getattr(requests, method.lower())(
                url, headers=headers, timeout=30, **kwargs
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"请求失败 [{method}] {path}: {e}")
            return None

    # --------------------------------------------------------
    # 获取主机列表
    # --------------------------------------------------------
    def get_hosts(self) -> list[dict]:
        """获取所有 VPS 主机列表"""
        data = self._request("GET", "/hosts", params={"page": 1, "limit": 100})
        if data and isinstance(data, dict):
            inner = data.get("data", {})
            if isinstance(inner, list):
                return inner
            if isinstance(inner, dict):
                # 尝试多种可能的字段名: host / hosts / list / result
                for key in ("host", "hosts", "list", "result", "servers"):
                    hosts = inner.get(key)
                    if isinstance(hosts, list) and hosts:
                        return hosts
        logger.warning(f"无法解析主机列表: {list(data.keys()) if isinstance(data, dict) else type(data)}")
        return []

    # --------------------------------------------------------
    # 获取单台主机状态
    # --------------------------------------------------------
    def get_host_status(self, host_id: str) -> str:
        """
        获取主机状态
        返回: 'on' 表示在线，其他表示离线
        """
        data = self._request(
            "GET", f"/hosts/{host_id}/module/status", params={"type": "host"}
        )
        if data and isinstance(data, dict):
            module_data = data.get("data", {})
            if isinstance(module_data, dict):
                return str(module_data.get("status", "")).lower()
            elif isinstance(module_data, str):
                return module_data.lower()
        return "unknown"

    # --------------------------------------------------------
    # 操作主机（开机 / 重启 / 关机）
    # --------------------------------------------------------
    def operate_host(self, host_id: str, action: str = "on") -> bool:
        """
        操作主机
        action: 'on' 开机 / 'reboot' 重启动 / 'hard_reboot' 硬重启 / 'off' 关机
        """
        data = self._request(
            "PUT", f"/hosts/{host_id}/module/hard_reboot",
            json={"action": action}
        )
        if data:
            logger.info(f"操作成功: 主机 {host_id} → {action}")
            return True
        else:
            logger.warning(f"操作失败或无响应: 主机 {host_id} → {action}")
            return False

    # --------------------------------------------------------
    # 飞书交互式卡片通知
    # --------------------------------------------------------
    @staticmethod
    def _build_card(header_title: str, header_color: str,
                    content_md: str, buttons: list[tuple[str, str]] | None = None) -> dict:
        """构建飞书交互式卡片消息

        Args:
            header_title: 卡片标题（纯文本）
            header_color: 标题栏颜色 (red/green/blue/yellow/orange/purple/turquoise)
            content_md: 卡片正文，支持 lark_md 格式（加粗/链接/换行等）
            buttons: 按钮列表，每项为 (按钮文字, 跳转URL)

        Returns:
            飞书 interactive 消息 payload
        """
        elements = [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": content_md},
            }
        ]

        if buttons:
            elements.append({
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": btn_text},
                        "url": btn_url,
                        "type": "default",
                    }
                    for btn_text, btn_url in buttons
                ],
            })

        # 底部备注（时间戳 + 来源标识）
        elements.append({
            "tag": "note",
            "elements": [
                {
                    "tag": "plain_text",
                    "content": f"IDC Auto-Restart · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                }
            ],
        })

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": header_title},
                    "template": header_color,
                },
                "elements": elements,
            },
        }

    def _send_card(self, header_title: str, header_color: str,
                   content_md: str, buttons: list[tuple[str, str]] | None = None):
        """发送飞书交互式卡片消息"""
        webhook = self.config.get("webhook_url", "")
        if not webhook:
            return

        card = self._build_card(header_title, header_color, content_md, buttons)
        try:
            resp = requests.post(webhook, json=card, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"飞书通知发送失败: HTTP {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"飞书通知发送异常: {e}")

    @staticmethod
    def _host_detail_md(host_name: str, host_ip: str, reboot_count: int, limit: int) -> str:
        """构建服务器详情的 lark_md 片段"""
        lines = [
            f"**服务器:** {host_name}",
        ]
        if host_ip:
            lines.append(f"**IP:** {host_ip}")
        lines.append(f"**今日重启:** 第 {reboot_count}/{limit} 次")
        return "\n".join(lines) + "\n"

    def _notify_offline_restart(self, host_name: str, host_ip: str,
                                 reboot_count: int, limit: int):
        """离线告警卡：发现服务器离线，已发送开机指令"""
        display = f"{host_name}({host_ip})" if host_ip else host_name
        content = (
            f"检测到服务器离线，已自动发送开机指令\n\n"
            f"{self._host_detail_md(host_name, host_ip, reboot_count, limit)}"
        )

        buttons = self._default_buttons()
        self._send_card(
            header_title=f"🔴 {display} 离线 · 自动开机",
            header_color="red",
            content_md=content,
            buttons=buttons,
        )

    def _notify_recovery_success(self, host_name: str, host_ip: str,
                                  reboot_count: int, limit: int):
        """恢复成功卡：服务器已恢复在线（这里是开机指令执行后，下次检测时发现的）"""
        # 注意：_handle_offline 调用 operate_host 后不会立即验证结果，
        # 这条卡片在 operate_host 返回成功后发送，标记为"已发送开机指令"。
        # 如果后续检测到已在线，我们发一条恢复确认。
        display = f"{host_name}({host_ip})" if host_ip else host_name
        content = (
            f"服务器已恢复在线 ✅\n\n"
            f"{self._host_detail_md(host_name, host_ip, reboot_count, limit)}"
        )
        self._send_card(
            header_title=f"🟢 {display} 已恢复在线",
            header_color="green",
            content_md=content,
            buttons=self._default_buttons(),
        )

    def _notify_startup_failed(self, host_name: str, host_ip: str,
                                reboot_count: int, limit: int):
        """开机失败卡"""
        display = f"{host_name}({host_ip})" if host_ip else host_name
        content = (
            f"**自动开机失败！**请立即手动检查服务器状态。\n\n"
            f"{self._host_detail_md(host_name, host_ip, reboot_count, limit)}"
            f"\n⚠️ 已尝试发送开机指令但 API 返回失败，建议登录控制台手动操作。"
        )
        self._send_card(
            header_title=f"⚠️ {display} 开机失败",
            header_color="orange",
            content_md=content,
            buttons=self._default_buttons(),
        )

    def _notify_limit_reached(self, host_name: str, host_ip: str,
                               reboot_count: int, limit: int):
        """重启次数达上限卡"""
        display = f"{host_name}({host_ip})" if host_ip else host_name
        content = (
            f"服务器今日自动重启次数已达上限，**不再自动开机**。\n\n"
            f"{self._host_detail_md(host_name, host_ip, reboot_count, limit)}"
            f"\n🔒 请登录控制台手动检查并开机。"
        )
        self._send_card(
            header_title=f"🔒 {display} 已达重启上限",
            header_color="purple",
            content_md=content,
            buttons=self._default_buttons(),
        )

    def _default_buttons(self) -> list[tuple[str, str]]:
        """默认操作按钮：控制台 + 仓库"""
        buttons = []
        console = self.config.get("idc_console_url", "")
        if console:
            buttons.append(("🔧 打开控制台", console))
        repo = self.config.get("repo_url", "")
        if repo:
            buttons.append(("📦 查看仓库", repo))
        return buttons

    # --------------------------------------------------------
    # 核心检测逻辑
    # --------------------------------------------------------
    def check_once(self) -> None:
        """执行一次全量检测"""
        logger.info("=" * 60)
        logger.info(f"开始检测... 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 确保已认证
        if not self.jwt_token:
            if not self.login():
                logger.error("登录失败，跳过本次检测")
                return

        hosts = self.get_hosts()
        if not hosts:
            logger.warning("未获取到任何主机列表")
            return

        logger.info(f"共发现 {len(hosts)} 台服务器")

        for host in hosts:
            host_id = str(host.get("id", ""))
            host_name = host.get("name") or host.get("hostname") or host.get("domain") or f"未知-{host_id}"
            host_ip = host.get("ip", "")

            if not host_id:
                continue

            # 查询状态
            status = self.get_host_status(host_id)
            is_online = status == "on"

            display_name = f"{host_name}({host_ip})" if host_ip else host_name
            logger.info(f"[{display_name}] 状态: {'✅ 在线' if is_online else '❌ 离线'} ({status})")

            if not is_online:
                self._handle_offline(host_id, display_name, host_name, host_ip)

        logger.info(f"本次检测完成\n")

    def _handle_offline(self, host_id: str, display_name: str,
                         host_name: str, host_ip: str) -> None:
        """处理离线主机"""
        limit = self.config.get("daily_reboot_limit", 3)
        current_count = self.reboot_counts.get(host_id, 0)

        if current_count >= limit:
            logger.warning(
                f"[{display_name}] 今日已自动重启 {current_count} 次 "
                f"(上限 {limit})，不再尝试"
            )
            self._notify_limit_reached(host_name, host_ip, current_count, limit)
            return

        # 执行开机
        logger.warning(f"[{display_name}] 检测到离线，正在自动开机...")
        success = self.operate_host(host_id, "on")

        if success:
            current_count += 1
            self.reboot_counts[host_id] = current_count
            logger.info(
                f"[{display_name}] 已自动开机 "
                f"(今日第 {current_count}/{limit} 次)"
            )
            self._notify_offline_restart(host_name, host_ip, current_count, limit)
        else:
            logger.error(f"[{display_name}] 自动开机失败！请手动检查")
            self._notify_startup_failed(host_name, host_ip, current_count, limit)

    # --------------------------------------------------------
    # 主循环
    # --------------------------------------------------------
    def run(self, once: bool = False) -> None:
        """运行监控循环"""
        logger.info("*" * 60)
        logger.info("核云IDC 自动监测重启程序 启动")
        logger.info(f"检测间隔: {self.config['check_interval']} 秒")
        logger.info(f"每日重启上限: {self.config['daily_reboot_limit']} 次/台")
        logger.info("*" * 60)

        # 首次登录
        if not self.login():
            logger.error("初始登录失败，程序退出")
            sys.exit(1)

        while self.running:
            try:
                self.check_once()
            except Exception as e:
                logger.error(f"检测过程异常: {e}", exc_info=True)

            if once:
                break

            # 等待下一次检测
            interval = self.config["check_interval"]
            for _ in range(interval):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("监控程序已停止")


# ============================================================
# 入口
# ============================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="核云IDC 服务器自动监测重启程序")
    parser.add_argument("--once", action="store_true", help="只执行一次检测后退出")
    parser.add_argument("--config", type=str, help="JSON 配置文件路径")
    args = parser.parse_args()

    # 支持从文件加载配置
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            file_config = json.load(f)
            CONFIG.update(file_config)

    # 环境变量覆盖（GitHub Actions 通过 Secrets 注入；本地用 config.json 即可）
    env_map = {
        "IDC_ACCOUNT": "api_account",
        "IDC_API_KEY": "api_password",
        "IDC_WEBHOOK": "webhook_url",
        "IDC_BASE_URL": "api_base_url",
        "IDC_CONSOLE_URL": "idc_console_url",
        "IDC_REPO_URL": "repo_url",
        "IDC_CHECK_INTERVAL": "check_interval",
        "IDC_DAILY_REBOOT_LIMIT": "daily_reboot_limit",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val:
            if cfg_key in ("check_interval", "daily_reboot_limit"):
                try:
                    val = int(val)
                except ValueError:
                    continue
            CONFIG[cfg_key] = val

    # 必填项检查
    if not CONFIG["api_account"] or not CONFIG["api_password"]:
        print("""
╔════════════════════════════════════════╗
║  使用前请先配置 API 凭据              ║
║                                        ║
║  方法一: 编辑本文件顶部的 CONFIG 字典  ║
║  方法二: 创建 config.json 并传入        ║
║     --config config.json               ║
║                                        ║
║  api_account:  你的手机号/邮箱          ║
║  api_password: 你的 API Key             ║
║  （在核云控制台「个人中心」生成）      ║
╚════════════════════════════════════════╝
""")
        sys.exit(1)

    monitor = IDCMonitor(CONFIG)
    monitor.run(once=args.once)


if __name__ == "__main__":
    main()
