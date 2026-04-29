from argparse import Namespace

from util import ConfigDB, GLOBAL_COOKIE_PATH
from util.BiliRequest import BiliRequest


def check_cookie_cmd(args: Namespace):
    cookies_path = (
        getattr(args, "cookies_path", None)
        or ConfigDB.get("cookies_path")
        or GLOBAL_COOKIE_PATH
    )
    request = BiliRequest(
        cookies_config_path=cookies_path,
        proxy=getattr(args, "https_proxys", "none"),
    )
    state = request.check_login_state()
    print(state.get("message", "检测完成"))
    if state.get("valid"):
        return
    raise SystemExit(1)
