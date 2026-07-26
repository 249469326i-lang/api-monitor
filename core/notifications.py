"""
通知模块 - Windows Toast + Webhook + 应用内通知
"""

import json
import time
import threading
import urllib.request
import urllib.error
from typing import Dict, Optional
from . import db


def notify(event_type: str, title: str, message: str, provider_id: int = None):
    """
    统一通知入口：同时触发应用内通知、Windows Toast、Webhook

    Args:
        event_type: 事件类型 (status_change / failover / test_complete)
        title: 通知标题
        message: 通知内容
        provider_id: 关联的供应商 ID (可选)
    """
    # 1. 应用内通知（始终写入）
    try:
        db.add_notification(event_type, title, message, provider_id)
    except Exception:
        pass

    # 2. Windows Toast
    _send_toast(event_type, title, message)

    # 3. Webhook
    _send_webhook(event_type, title, message, provider_id)


def _send_toast(event_type: str, title: str, message: str):
    """发送 Windows Toast 通知"""
    # 检查设置中是否启用该类通知
    setting_key = f"notify_{event_type}"
    if db.get_setting(setting_key) != "1":
        return

    try:
        # 使用 PowerShell 发送 Windows Toast 通知（无额外依赖）。
        # 通知内容可能包含上游 API 返回的任意文本，绝不能拼进脚本文本
        # （here-string 中 $() 会被展开执行）。这里在 Python 侧完成 XML 转义，
        # 整段 XML 经环境变量传入；脚本本身是固定的，用 -EncodedCommand 传递。
        import base64
        import os
        import subprocess
        from xml.sax.saxutils import escape as _xml_escape

        toast_xml = (
            '<toast duration="short">'
            "<visual>"
            '<binding template="ToastGeneric">'
            "<text>API Monitor</text>"
            f"<text>{_xml_escape(f'{title}: {message}')}</text>"
            "</binding>"
            "</visual>"
            '<audio src="ms-winsoundevent:Notification.Default"/>'
            "</toast>"
        )

        ps_script = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null\n"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null\n"
            "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
            "$xml.LoadXml($env:API_MONITOR_TOAST_XML)\n"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)\n"
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("API Monitor").Show($toast)\n'
        )
        encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        env = os.environ.copy()
        env["API_MONITOR_TOAST_XML"] = toast_xml

        # 在后台线程中执行，避免阻塞主线程
        threading.Thread(
            target=lambda: subprocess.run(
                ["powershell", "-NoProfile", "-EncodedCommand", encoded],
                capture_output=True,
                timeout=5,
                env=env,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            ),
            daemon=True,
        ).start()

    except Exception:
        pass


def _send_webhook(event_type: str, title: str, message: str, provider_id: int = None):
    """通过 Webhook 发送通知"""
    webhook_url = db.get_setting("webhook_url")
    if not webhook_url:
        return

    # 检查该事件类型是否在 webhook 订阅列表中
    subscribed = db.get_setting("webhook_events") or ""
    if event_type not in subscribed:
        return

    # 构造 payload
    payload = {
        "event": event_type,
        "title": title,
        "message": message,
        "provider_id": provider_id,
        "timestamp": int(time.time()),
        "app": "API Monitor",
    }

    # 同时支持 Slack / 飞书 / 钉钉格式
    # Slack: {"text": "..."}
    # 飞书: {"msg_type": "text", "content": {"text": "..."}}
    # 钉钉: {"msgtype": "text", "text": {"content": "..."}}
    # 通用: 发原始 JSON，让用户自行配置

    def _do_send():
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                pass  # 成功即可
        except Exception:
            pass  # Webhook 失败不阻塞主流程

    threading.Thread(target=_do_send, daemon=True).start()
