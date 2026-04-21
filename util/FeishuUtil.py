import base64
import hashlib
import hmac
import json
import time

import requests

from util.Notifier import NotifierBase


def _gen_sign(secret: str, timestamp: int) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def build_feishu_webhook_url(token_or_url: str) -> str:
    token_or_url = token_or_url.strip()
    if token_or_url.startswith("http://") or token_or_url.startswith("https://"):
        return token_or_url
    return f"https://open.feishu.cn/open-apis/bot/v2/hook/{token_or_url}"


def send_feishu_text(webhook: str, title: str, message: str, secret: str | None = None) -> requests.Response:
    payload: dict = {
        "msg_type": "text",
        "content": {"text": f"[{title}]\n{message}"},
    }
    if secret:
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _gen_sign(secret, ts)
    return requests.post(
        build_feishu_webhook_url(webhook),
        headers={"Content-Type": "application/json"},
        data=json.dumps(payload),
        timeout=10,
    )


class FeishuNotifier(NotifierBase):
    """飞书自定义机器人通知。

    webhook: 机器人 Webhook 地址或仅 token 部分。
    secret:  可选，机器人安全设置开启「签名校验」时填写。
    """

    def __init__(
        self,
        webhook: str,
        title: str,
        content: str,
        secret: str | None = None,
        interval_seconds: int = 10,
        duration_minutes: int = 10,
    ):
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.webhook = webhook
        self.secret = secret or None

    def send_message(self, title, message):
        resp = send_feishu_text(self.webhook, title, message, self.secret)
        try:
            data = resp.json()
        except ValueError:
            resp.raise_for_status()
            return
        if data.get("code", 0) != 0 and data.get("StatusCode", 0) != 0:
            raise RuntimeError(f"Feishu 推送失败: {data}")


def test_connection(webhook: str, secret: str | None = None) -> tuple[bool, str]:
    if not webhook:
        return False, "未配置 Webhook"
    try:
        resp = send_feishu_text(webhook, "🎫 抢票测试", "飞书测试推送，如收到说明配置正常。", secret)
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 200 and data.get("code", 0) == 0:
            return True, "测试消息已发送"
        return False, f"状态码 {resp.status_code}, 响应 {data or resp.text}"
    except Exception as e:
        return False, f"异常: {e}"
