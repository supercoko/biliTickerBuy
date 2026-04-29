import json
import os
import subprocess
import sys
import time
from email.utils import parsedate_to_datetime
from random import randint, uniform
from datetime import datetime, timezone
from json import JSONDecodeError
import shutil
import qrcode
from loguru import logger

from requests import HTTPError, RequestException

from util import ERRNO_DICT, time_service
from util.Notifier import NotifierManager, NotifierConfig
from util.BiliRequest import BiliRequest
from util.RandomMessages import get_random_fail_message
from util.CTokenUtil import CTokenGenerator


base_url = "https://show.bilibili.com"

# errno 属于「没票 / 正在售罄」，可在捡漏模式下继续轮询
SOLD_OUT_ERRNOS = {100001, 100009, 100039}
# errno 属于「系统繁忙」，拉长间隔后重试，不算失败
BUSY_ERRNOS = {3, 900001, 900002}
HTTP_THROTTLE_STATUSES = {412, 429}
SCAVENGE_MIN_INTERVAL_MS = 2500
RATE_LIMIT_BASE_BACKOFF_MS = 15000
RATE_LIMIT_MAX_BACKOFF_MS = 120000
RATE_LIMIT_BACKOFF_STEPS = 4


def _get_http_status(exc: RequestException) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _retry_after_ms(exc: RequestException) -> int | None:
    response = getattr(exc, "response", None)
    if response is None:
        return None
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        return max(0, int(float(retry_after) * 1000))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(seconds * 1000))


def _rate_limit_backoff_ms(
    exc: RequestException,
    rate_limit_attempt: int,
    interval_ms: int,
    scavenge_interval_ms: int,
) -> int:
    retry_after = _retry_after_ms(exc)
    if retry_after is not None:
        return min(RATE_LIMIT_MAX_BACKOFF_MS, max(1000, retry_after))
    status = _get_http_status(exc)
    base_floor = 60000 if status == 412 else RATE_LIMIT_BASE_BACKOFF_MS
    base = max(base_floor, interval_ms * 3, scavenge_interval_ms * 2)
    exponent = min(max(0, rate_limit_attempt - 1), RATE_LIMIT_BACKOFF_STEPS)
    return min(RATE_LIMIT_MAX_BACKOFF_MS, int(base * (2**exponent)))


def _http_throttle_label(status: int | None) -> str:
    if status == 412:
        return "风控拦截"
    if status == 429:
        return "请求过频"
    return "请求受限"


def get_qrcode_url(_request, order_id) -> str:
    url = f"{base_url}/api/ticket/order/getPayParam?order_id={order_id}"
    data = _request.get(url).json()
    if data.get("errno", data.get("code")) == 0:
        return data["data"]["code_url"]
    raise ValueError("获取二维码失败")


def _format_countdown(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}小时{minutes}分{secs}秒"


def _wait_until_start(time_start: str):
    """倒计时等待。最后 500ms 使用 busy-wait 提高精度（Windows sleep 精度差）。"""
    if not time_start:
        return

    timeoffset = time_service.get_timeoffset()
    yield "0) 等待开始时间"
    yield f"时间偏差已被设置为: {timeoffset}s"

    try:
        target_time = datetime.strptime(time_start, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        target_time = datetime.strptime(time_start, "%Y-%m-%dT%H:%M")

    yield f"计划抢票开始时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}"

    time_difference = target_time.timestamp() - time.time() + timeoffset
    end_time = time.perf_counter() + time_difference
    next_report_at = float("inf")
    while True:
        remaining = end_time - time.perf_counter()
        if remaining <= 0:
            return
        if remaining <= next_report_at:
            yield f"距离开始抢票还有: {_format_countdown(remaining)}"
            next_report_at = max(0.0, remaining - 5)
        # 最后 500ms busy-wait 提升首包精度
        if remaining > 0.5:
            time.sleep(min(0.3, remaining - 0.5))
        else:
            # busy loop
            while time.perf_counter() < end_time:
                pass
            return


def _build_token_payload(tickets_info: dict) -> dict:
    return {
        "count": tickets_info["count"],
        "screen_id": tickets_info["screen_id"],
        "order_type": 1,
        "project_id": tickets_info["project_id"],
        "sku_id": tickets_info["sku_id"],
        "token": "",
        "newRisk": True,
    }


def _build_order_payload(tickets_info: dict, token: str) -> dict:
    payload = dict(tickets_info)
    payload["again"] = 1
    payload["token"] = token
    payload["timestamp"] = int(time.time()) * 1000
    payload.pop("detail", None)
    return payload


def _is_create_success(ret: dict, err: int) -> bool:
    if err in {100048, 100079}:
        return True
    resp_message = str(ret.get("msg", ret.get("message", "")) or "")
    return err == 0 and "defaultBBR" not in resp_message


def _jittered_sleep(interval_ms: float, jitter_ratio: float):
    """基础间隔 ± jitter_ratio 的抖动，避免固定频率被风控识别。"""
    base = interval_ms / 1000.0
    if jitter_ratio <= 0:
        time.sleep(base)
        return
    delta = base * jitter_ratio
    time.sleep(max(0.05, base + uniform(-delta, delta)))


def buy_stream(
    tickets_info,
    time_start,
    interval,
    notifier_config,
    https_proxys,
    show_random_message=True,
    show_qrcode=True,
    max_retries: int = 200,
    interval_jitter: float = 0.25,
    scavenge_mode: bool = False,
    scavenge_interval: int = 3000,
    scavenge_max_retries: int = 0,
):
    """抢票主循环。

    Parameters
    ----------
    max_retries: 普通重试次数上限（达到后会重新 prepare）。
                 仅用于「非捡漏错误码」的重试计数。
    interval_jitter: 每次下单间隔的抖动比例，0.25 表示 ±25%。
    scavenge_mode: 捡漏模式——遇到「无票/库存不足/活动收摊」也持续轮询，
                   配合较长的 ``scavenge_interval`` 应对退票释放。
    scavenge_interval: 捡漏模式下，「无票」时的轮询间隔（ms）。
    scavenge_max_retries: 捡漏专用重试上限，独立于 ``max_retries``。
                          ``<= 0`` 表示无限轮询（常用于长时间守株待兔）。
    """
    isRunning = True
    interval = int(interval or 1000)
    scavenge_interval = int(scavenge_interval or 3000)
    tickets_info = json.loads(tickets_info)
    detail = tickets_info["detail"]
    cookies = tickets_info["cookies"]
    tickets_info.pop("cookies", None)
    tickets_info["buyer_info"] = json.dumps(tickets_info["buyer_info"])
    tickets_info["deliver_info"] = json.dumps(tickets_info["deliver_info"])
    logger.info(f"使用代理：{https_proxys}")
    _request = BiliRequest(cookies=cookies, proxy=https_proxys)

    is_hot_project = bool(tickets_info.get("is_hot_project", False))
    token_payload = _build_token_payload(tickets_info)

    yield from _wait_until_start(time_start)

    if scavenge_mode and scavenge_interval < SCAVENGE_MIN_INTERVAL_MS:
        yield (
            f"捡漏间隔 {scavenge_interval}ms 过低，已自动提升到 "
            f"{SCAVENGE_MIN_INTERVAL_MS}ms，降低 HTTP 429/风控概率"
        )
        scavenge_interval = SCAVENGE_MIN_INTERVAL_MS

    if scavenge_mode:
        limit_desc = str(scavenge_max_retries) if scavenge_max_retries > 0 else "∞"
        yield (
            f"🔍 捡漏模式已开启（间隔 {scavenge_interval}ms ± "
            f"{int(interval_jitter*100)}%, 上限 {limit_desc} 次）"
        )

    rate_limit_attempt = 0
    total_scavenge_attempt = 0
    while isRunning:
        try:
            yield "1）订单准备"
            if is_hot_project:
                ctoken_generator = CTokenGenerator(time.time(), 0, randint(2000, 10000))
                token_payload["token"] = ctoken_generator.generate_ctoken(
                    is_create_v2=False
                )
            request_result_normal = _request.post(
                url=f"{base_url}/api/ticket/order/prepare?project_id={tickets_info['project_id']}",
                data=token_payload,
                isJson=True,
            )
            request_result = request_result_normal.json()
            yield f"请求头: {request_result_normal.headers} // 请求体: {request_result}"
            yield "2）创建订单"
            payload = _build_order_payload(
                tickets_info, request_result["data"]["token"]
            )

            result = None
            attempt = 0
            scavenge_limit_str = (
                str(scavenge_max_retries) if scavenge_max_retries > 0 else "∞"
            )
            exhausted = False
            while isRunning:
                try:
                    url = f"{base_url}/api/ticket/order/createV2?project_id={tickets_info['project_id']}"
                    if is_hot_project:
                        payload["ctoken"] = ctoken_generator.generate_ctoken(  # type: ignore
                            is_create_v2=True
                        )
                        ptoken = request_result["data"]["ptoken"] or ""
                        payload["ptoken"] = ptoken
                        payload["orderCreateUrl"] = (
                            "https://show.bilibili.com/api/ticket/order/createV2"
                        )
                        url += "&ptoken=" + ptoken
                    ret = _request.post(
                        url=url,
                        data=payload,
                        isJson=True,
                    ).json()
                    if rate_limit_attempt > 0:
                        rate_limit_attempt = max(0, rate_limit_attempt - 1)
                    err = int(ret.get("errno", ret.get("code")))
                    if err == 100034:
                        yield f"更新票价为：{ret['data']['pay_money'] / 100}"
                        payload["pay_money"] = ret["data"]["pay_money"]
                    if _is_create_success(ret, err):
                        yield "请求成功，停止重试"
                        result = (ret, err)
                        break
                    if err == 100051:
                        # token 过期，退出重试重新 prepare
                        break
                    # 捡漏分支：使用独立计数器，不消耗 max_retries
                    if err in SOLD_OUT_ERRNOS:
                        if scavenge_mode:
                            total_scavenge_attempt += 1
                            if (
                                scavenge_max_retries > 0
                                and total_scavenge_attempt > scavenge_max_retries
                            ):
                                yield (
                                    f"捡漏达上限 {scavenge_max_retries} 次，停止抢票"
                                )
                                return
                            yield (
                                f"[捡漏 {total_scavenge_attempt}/{scavenge_limit_str}] "
                                f"[{err}]({ERRNO_DICT.get(err, '未知')}) 等待退票..."
                            )
                            _jittered_sleep(scavenge_interval, interval_jitter)
                            continue
                        # 非捡漏模式下 100039 表示活动彻底结束，退出
                        if err == 100039:
                            yield f"活动已结束 ({err})，停止抢票"
                            return
                    if err in BUSY_ERRNOS:
                        busy_backoff_ms = max(interval * 2, 1500)
                        if scavenge_mode:
                            busy_backoff_ms = max(
                                busy_backoff_ms,
                                min(scavenge_interval, 5000),
                            )
                        yield (
                            f"[尝试] 服务繁忙 [{err}]，"
                            f"退避 {busy_backoff_ms / 1000:.1f}s"
                        )
                        _jittered_sleep(busy_backoff_ms, interval_jitter)
                        continue

                    attempt += 1
                    if attempt > max_retries:
                        exhausted = True
                        break
                    yield f"[尝试 {attempt}/{max_retries}]  [{err}]({ERRNO_DICT.get(err, '未知错误码')}) | {ret}"
                    _jittered_sleep(interval, interval_jitter)

                except RequestException as e:
                    status = _get_http_status(e)
                    if status in HTTP_THROTTLE_STATUSES:
                        rate_limit_attempt += 1
                        backoff_ms = _rate_limit_backoff_ms(
                            e,
                            rate_limit_attempt,
                            int(interval),
                            int(scavenge_interval if scavenge_mode else 0),
                        )
                        yield (
                            f"[限流 {rate_limit_attempt}] HTTP {status} "
                            f"{_http_throttle_label(status)}，"
                            f"冷却 {backoff_ms / 1000:.1f}s 后继续"
                        )
                        _jittered_sleep(backoff_ms, interval_jitter)
                        continue

                    attempt += 1
                    if attempt > max_retries:
                        exhausted = True
                        break
                    yield f"[尝试 {attempt}/{max_retries}] 请求异常: {e}"
                    _jittered_sleep(interval, interval_jitter)

                except Exception as e:
                    attempt += 1
                    if attempt > max_retries:
                        exhausted = True
                        break
                    yield f"[尝试 {attempt}/{max_retries}] 未知异常: {e}"
                    _jittered_sleep(interval, interval_jitter)

            if not isRunning:
                yield "抢票结束"
                break
            if exhausted:
                if show_random_message:
                    yield f"群友说👴： {get_random_fail_message()}"
                yield "重试次数过多，重新准备订单"
                continue
            if result is None:
                yield "token过期，需要重新准备订单"
                continue

            request_result, errno = result
            if errno == 0:
                notifierManager = NotifierManager.create_from_config(
                    config=notifier_config,
                    title="抢票成功",
                    content=f"bilibili会员购，请尽快前往订单中心付款: {detail}",
                )

                notifierManager.start_all()

                yield "3）抢票成功，弹出付款二维码"
                qrcode_url = get_qrcode_url(
                    _request,
                    request_result["data"]["orderId"],
                )
                if show_qrcode:
                    qr_gen = qrcode.QRCode()
                    qr_gen.add_data(qrcode_url)
                    qr_gen.make(fit=True)
                    qr_gen_image = qr_gen.make_image()
                    qr_gen_image.show()  # type: ignore
                else:
                    yield "PAYMENT_QR_URL={0}".format(qrcode_url)
                break
            if errno == 100079:
                yield "有重复订单，停止重试"
                break
        except JSONDecodeError as e:
            yield f"配置文件格式错误: {e}"
        except HTTPError as e:
            status = _get_http_status(e)
            if status in HTTP_THROTTLE_STATUSES:
                rate_limit_attempt += 1
                backoff_ms = _rate_limit_backoff_ms(
                    e,
                    rate_limit_attempt,
                    int(interval),
                    int(scavenge_interval if scavenge_mode else 0),
                )
                yield (
                    f"订单准备被限制：HTTP {status} "
                    f"{_http_throttle_label(status)}，冷却 "
                    f"{backoff_ms / 1000:.1f}s 后重试"
                )
                _jittered_sleep(backoff_ms, interval_jitter)
                continue
            logger.exception(e)
            yield f"请求错误: {e}"
        except Exception as e:
            logger.exception(e)
            yield f"程序异常: {repr(e)}"


def buy(
    tickets_info,
    time_start,
    interval,
    audio_path,
    pushplusToken,
    serverchanKey,
    barkToken,
    https_proxys,
    serverchan3ApiUrl=None,
    ntfy_url=None,
    ntfy_username=None,
    ntfy_password=None,
    feishu_webhook=None,
    feishu_secret=None,
    show_random_message=True,
    show_qrcode=True,
    max_retries: int = 200,
    interval_jitter: float = 0.25,
    scavenge_mode: bool = False,
    scavenge_interval: int = 3000,
    scavenge_max_retries: int = 0,
):
    notifier_config = NotifierConfig(
        serverchan_key=serverchanKey,
        serverchan3_api_url=serverchan3ApiUrl,
        pushplus_token=pushplusToken,
        bark_token=barkToken,
        ntfy_url=ntfy_url,
        ntfy_username=ntfy_username,
        ntfy_password=ntfy_password,
        feishu_webhook=feishu_webhook,
        feishu_secret=feishu_secret,
        audio_path=audio_path,
    )

    for msg in buy_stream(
        tickets_info,
        time_start,
        interval,
        notifier_config,
        https_proxys,
        show_random_message,
        show_qrcode,
        max_retries=max_retries,
        interval_jitter=interval_jitter,
        scavenge_mode=scavenge_mode,
        scavenge_interval=scavenge_interval,
        scavenge_max_retries=scavenge_max_retries,
    ):
        logger.info(msg)


def buy_new_terminal(
    endpoint_url,
    tickets_info,
    time_start,
    interval,
    audio_path,
    pushplusToken,
    serverchanKey,
    barkToken,
    https_proxys,
    serverchan3ApiUrl=None,
    ntfy_url=None,
    ntfy_username=None,
    ntfy_password=None,
    feishu_webhook=None,
    feishu_secret=None,
    show_random_message=True,
    terminal_ui="网页",
    max_retries: int = 200,
    interval_jitter: float = 0.25,
    scavenge_mode: bool = False,
    scavenge_interval: int = 3000,
    scavenge_max_retries: int = 0,
) -> subprocess.Popen:
    command = None

    # 1️⃣ PyInstaller / frozen
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        # 2️⃣ 源码模式：检查「当前脚本目录」是否有 main.py
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_py = os.path.join(script_dir, "main.py")

        if os.path.exists(main_py):
            command = [sys.executable, main_py]
        # 3️⃣ 兜底：使用 btb（pip / pipx）
        else:
            btb_path = shutil.which("btb")
            if not btb_path:
                raise RuntimeError("Cannot find main.py or btb command")

            command = [btb_path]
    command.extend(["buy", tickets_info])
    if interval is not None:
        command.extend(["--interval", str(interval)])
    if time_start:
        command.extend(["--time_start", time_start])
    if audio_path:
        command.extend(["--audio_path", audio_path])
    if pushplusToken:
        command.extend(["--pushplusToken", pushplusToken])
    if serverchanKey:
        command.extend(["--serverchanKey", serverchanKey])
    if serverchan3ApiUrl:
        command.extend(["--serverchan3ApiUrl", serverchan3ApiUrl])
    if barkToken:
        command.extend(["--barkToken", barkToken])
    if ntfy_url:
        command.extend(["--ntfy_url", ntfy_url])
    if ntfy_username:
        command.extend(["--ntfy_username", ntfy_username])
    if ntfy_password:
        command.extend(["--ntfy_password", ntfy_password])
    if feishu_webhook:
        command.extend(["--feishu_webhook", feishu_webhook])
    if feishu_secret:
        command.extend(["--feishu_secret", feishu_secret])
    if https_proxys:
        command.extend(["--https_proxys", https_proxys])
    if not show_random_message:
        command.extend(["--hide_random_message"])
    command.extend(["--max_retries", str(max_retries)])
    command.extend(["--interval_jitter", str(interval_jitter)])
    if scavenge_mode:
        command.append("--scavenge_mode")
        command.extend(["--scavenge_interval", str(scavenge_interval)])
        command.extend(["--scavenge_max_retries", str(scavenge_max_retries)])
    if terminal_ui == "网页":
        command.append("--web")
    command.extend(["--endpoint_url", endpoint_url])
    if terminal_ui == "网页":
        proc = subprocess.Popen(command)
    else:
        if os.name == "nt":
            # Windows：优先使用 Windows Terminal（wt.exe），以 tab 形式
            # 复用当前窗口（-w 0），多配置时每个抢票任务一个 tab，
            # 比 cmd/powershell 各开一个独立窗口整洁得多。
            wt_path = shutil.which("wt.exe") or shutil.which("wt")
            if wt_path:
                # 用 `--` 分隔，防止 wt 把 Python 的长选项当成自己的子命令；
                # tab 标题由子进程启动时通过 `title` 命令自行设置（见 app_cmd/buy.py）。
                wt_cmd = [wt_path, "-w", "0", "new-tab", "--"] + command
                try:
                    proc = subprocess.Popen(wt_cmd)
                except (OSError, subprocess.SubprocessError):
                    proc = subprocess.Popen(
                        command, creationflags=subprocess.CREATE_NEW_CONSOLE
                    )
            else:
                proc = subprocess.Popen(
                    command, creationflags=subprocess.CREATE_NEW_CONSOLE
                )
        else:
            # macOS / Linux：沿用原有行为，走默认终端
            proc = subprocess.Popen(command)
    return proc
