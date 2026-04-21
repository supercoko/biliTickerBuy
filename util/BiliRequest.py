import json
import random
import time

import loguru
import requests

from util.CookieManager import CookieManager


# 桌面浏览器 UA 池，412 时轮换，降低指纹稳定性
_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]


class BiliRequest:
    """带风控友好策略的请求封装。

    风控 (HTTP 412) 处理逻辑：
    - 非递归循环，避免栈溢出
    - 切换代理 + 轮换 User-Agent
    - 指数退避（5s → 10s → 20s，带 ±20% 抖动，最长 60s）
    - 连续 412 次数超过阈值才长眠，避免首次命中就 sleep 60s
    """

    MAX_RISK_RETRY = 6
    BASE_RISK_BACKOFF = 5.0
    MAX_RISK_BACKOFF = 60.0

    def __init__(
        self, headers=None, cookies=None, cookies_config_path=None, proxy: str = "none"
    ):
        self.session = requests.Session()
        proxy_list = (
            [v.strip() for v in proxy.split(",") if len(v.strip()) != 0]
            if proxy
            else []
        )
        if len(proxy_list) == 0:
            proxy_list = ["none"]
        self.proxy_list = proxy_list
        self.now_proxy_idx = 0
        self._apply_proxy()

        self.cookieManager = CookieManager(cookies_config_path, cookies)
        self.headers = headers or {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5,ja;q=0.4",
            "content-type": "application/x-www-form-urlencoded",
            "cookie": "",
            "referer": "https://show.bilibili.com/",
            "priority": "u=1, i",
            "user-agent": random.choice(_UA_POOL),
        }
        self.request_count = 0

    def _apply_proxy(self):
        current_proxy = self.proxy_list[self.now_proxy_idx]
        if current_proxy == "none":
            self.session.proxies = {}
        else:
            self.session.proxies = {"http": current_proxy, "https": current_proxy}

    def switch_proxy(self):
        self.now_proxy_idx = (self.now_proxy_idx + 1) % len(self.proxy_list)
        self._apply_proxy()

    def _rotate_ua(self):
        self.headers["user-agent"] = random.choice(_UA_POOL)

    def _backoff_sleep(self, attempt: int):
        base = min(
            self.BASE_RISK_BACKOFF * (2 ** max(0, attempt - 1)),
            self.MAX_RISK_BACKOFF,
        )
        jitter = base * random.uniform(-0.2, 0.2)
        delay = max(1.0, base + jitter)
        loguru.logger.warning(f"412 风控，退避 {delay:.1f}s 后重试（第 {attempt} 次）")
        time.sleep(delay)

    def _request(self, method: str, url: str, data=None, isJson=False):
        if isJson:
            self.headers["content-type"] = "application/json"
            body = json.dumps(data) if data is not None else None
        else:
            self.headers["content-type"] = "application/x-www-form-urlencoded"
            body = data

        risk_attempt = 0
        while True:
            self.headers["cookie"] = self.cookieManager.get_cookies_str()
            if method == "GET":
                response = self.session.get(
                    url, data=body, headers=self.headers, timeout=10
                )
            else:
                response = self.session.post(
                    url, data=body, headers=self.headers, timeout=10
                )

            if response.status_code != 412:
                break

            risk_attempt += 1
            self.request_count += 1
            if risk_attempt >= self.MAX_RISK_RETRY:
                loguru.logger.error(
                    f"连续 {risk_attempt} 次 412 风控，放弃该请求"
                )
                response.raise_for_status()
            self.switch_proxy()
            self._rotate_ua()
            self._backoff_sleep(risk_attempt)

        response.raise_for_status()
        self.request_count = 0
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("msg", "") == "请先登录":
            raise RuntimeError("当前未登录，请重新登陆")
        return response

    def get(self, url, data=None, isJson=False):
        return self._request("GET", url, data=data, isJson=isJson)

    def post(self, url, data=None, isJson=False):
        return self._request("POST", url, data=data, isJson=isJson)

    def get_request_name(self):
        try:
            if not self.cookieManager.have_cookies():
                loguru.logger.warning("获取用户名失败，请重新登录")
                return "未登录"
            result = self.get("https://api.bilibili.com/x/web-interface/nav").json()
            return result["data"]["uname"]
        except Exception:
            return "未登录"

    # 兼容旧调用
    def count_and_sleep(self, threshold=60, sleep_time=60):
        self.request_count += 1
        if self.request_count % threshold == 0:
            loguru.logger.info(f"达到 {threshold} 次请求 412，休眠 {sleep_time} 秒")
            time.sleep(sleep_time)

    def clear_request_count(self):
        self.request_count = 0
