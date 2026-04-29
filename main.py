import argparse
import os


def get_env_default(key: str, default, cast_func):
    return cast_func(os.environ.get(f"BTB_{key}", default))


def str_to_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def main():
    gradio_parent = argparse.ArgumentParser(add_help=False)
    gradio_parent.add_argument(
        "--share",
        action="store_true",
        default=get_env_default("SHARE", False, str_to_bool),
        help="Share Gradio app publicly (tunnel). Defaults to False.",
    )
    gradio_parent.add_argument(
        "--server_name",
        type=str,
        default=os.environ.get("BTB_SERVER_NAME", "127.0.0.1"),
        help='Server name for Gradio. Defaults to env "BTB_SERVER_NAME" or 127.0.0.1.',
    )
    gradio_parent.add_argument(
        "--port",
        type=int,
        default=os.environ.get("BTB_PORT", os.environ.get("GRADIO_SERVER_PORT", None)),
        help='Server port for Gradio. Defaults to env "BTB_PORT"/"GRADIO_SERVER_PORT" or 7860.',
    )

    parser = argparse.ArgumentParser(
        description=(
            "BiliTickerBuy\n\n"
            "Use `btb buy` to buy tickets directly in the command line.\n"
            "Use `btb check-cookie` to validate the current login cookie.\n"
            "Run `btb` without arguments to open the UI.\n"
            "Run `btb buy -h` for `btb buy` detailed options."
        ),
        epilog=(
            "Examples:\n"
            "  btb buy tickets.json\n"
            "  btb buy tickets.json --interval 500\n\n"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        parents=[gradio_parent],
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="Available Commands",
        metavar="{buy,check-cookie}",
        description="Use one of the following commands",
    )
    buy_parser = subparsers.add_parser(
        "buy",
        help="Buy tickets directly in the command line",
        parents=[gradio_parent],
    )
    check_cookie_parser = subparsers.add_parser(
        "check-cookie",
        help="Validate the current Bilibili login cookie",
    )
    check_cookie_parser.add_argument(
        "--cookies_path",
        type=str,
        default=os.environ.get("BTB_COOKIES_PATH", ""),
        help="Cookie store path. Defaults to BTB_COOKIES_PATH or the configured cookie file.",
    )
    check_cookie_parser.add_argument(
        "--https_proxys",
        type=str,
        default=os.environ.get("BTB_HTTPS_PROXYS", "none"),
        help="HTTPS proxy, e.g. http://127.0.0.1:8080",
    )
    # ===== Buy Core =====
    buy_core = buy_parser.add_argument_group("Buy Core Options")
    buy_core.add_argument(
        "tickets_info",
        type=str,
        help="Ticket information in JSON format or a path to a JSON config file.",
    )
    buy_core.add_argument(
        "--interval",
        type=int,
        default=1000,
        help="Interval time (ms). Defaults to 1000 if omitted.",
    )
    buy_core.add_argument(
        "--endpoint_url",
        type=str,
        default=os.environ.get("BTB_ENDPOINT_URL", ""),
        help="Endpoint URL.",
    )
    buy_core.add_argument(
        "--time_start",
        type=str,
        default=os.environ.get("BTB_TIME_START", ""),
        help="Start time (optional).",
    )
    buy_core.add_argument(
        "--https_proxys",
        type=str,
        default=os.environ.get("BTB_HTTPS_PROXYS", "none"),
        help="HTTPS proxy, e.g. http://127.0.0.1:8080",
    )

    # ===== Notifications =====
    notify = buy_parser.add_argument_group("Notification Options")

    notify.add_argument(
        "--audio_path",
        type=str,
        default=os.environ.get("BTB_AUDIO_PATH", ""),
        help="Path to audio file (optional).",
    )
    notify.add_argument(
        "--pushplusToken",
        type=str,
        default=os.environ.get("BTB_PUSHPLUSTOKEN", ""),
        help="PushPlus token (optional).",
    )
    notify.add_argument(
        "--serverchanKey",
        type=str,
        default=os.environ.get("BTB_SERVERCHANKEY", ""),
        help="ServerChan key (optional).",
    )
    notify.add_argument(
        "--serverchan3ApiUrl",
        type=str,
        default=os.environ.get("BTB_SERVERCHAN3APIURL", ""),
        help="ServerChan3 API URL (optional).",
    )
    notify.add_argument(
        "--barkToken",
        type=str,
        default=os.environ.get("BTB_BARKTOKEN", ""),
        help="Bark token (optional).",
    )
    notify.add_argument(
        "--ntfy_url",
        type=str,
        default=os.environ.get("BTB_NTFY_URL", ""),
        help="Ntfy server URL, e.g. https://ntfy.sh/topic",
    )
    notify.add_argument(
        "--ntfy_username",
        type=str,
        default=os.environ.get("BTB_NTFY_USERNAME", ""),
        help="Ntfy username (optional).",
    )
    notify.add_argument(
        "--ntfy_password",
        type=str,
        default=os.environ.get("BTB_NTFY_PASSWORD", ""),
        help="Ntfy password (optional).",
    )
    notify.add_argument(
        "--feishu_webhook",
        type=str,
        default=os.environ.get("BTB_FEISHU_WEBHOOK", ""),
        help="Feishu bot webhook URL or token (optional).",
    )
    notify.add_argument(
        "--feishu_secret",
        type=str,
        default=os.environ.get("BTB_FEISHU_SECRET", ""),
        help="Feishu signed-verification secret (optional).",
    )

    # ===== Anti risk-control / 捡漏 =====
    antirc = buy_parser.add_argument_group("Anti Risk-Control & Scavenge Options")
    antirc.add_argument(
        "--max_retries",
        type=int,
        default=int(os.environ.get("BTB_MAX_RETRIES", 200)),
        help="每轮 createV2 最大重试次数，达到后重新 prepare。默认 200。",
    )
    antirc.add_argument(
        "--interval_jitter",
        type=float,
        default=float(os.environ.get("BTB_INTERVAL_JITTER", 0.25)),
        help="每次下单间隔的随机抖动比例（0-1），默认 0.25 表示 ±25%%。",
    )
    antirc.add_argument(
        "--scavenge_mode",
        action="store_true",
        default=str_to_bool(os.environ.get("BTB_SCAVENGE_MODE", False)),
        help="开启捡漏模式：对无票/库存不足持续轮询，应对退票释放。",
    )
    antirc.add_argument(
        "--scavenge_interval",
        type=int,
        default=int(os.environ.get("BTB_SCAVENGE_INTERVAL", 3000)),
        help="捡漏模式下「无票」时的轮询间隔（ms），默认 3000。",
    )
    antirc.add_argument(
        "--scavenge_max_retries",
        type=int,
        default=int(os.environ.get("BTB_SCAVENGE_MAX_RETRIES", 0)),
        help="捡漏专用重试次数上限，独立于 --max_retries。0 或负数表示无限，默认 0（无限）。",
    )

    # ===== Runtime / UI =====
    runtime = buy_parser.add_argument_group("Runtime & UI Options")
    runtime.add_argument(
        "--web",
        action="store_true",
        help="Run with web UI instead of terminal output (useful on macOS).",
    )
    runtime.add_argument(
        "--hide_random_message",
        action="store_true",
        help="Hide random message when fail.",
    )

    args = parser.parse_args()
    if args.command == "buy":
        from app_cmd.buy import buy_cmd

        buy_cmd(args=args)
    elif args.command == "check-cookie":
        from app_cmd.auth import check_cookie_cmd

        check_cookie_cmd(args=args)
    else:
        from app_cmd.ticker import ticker_cmd

        ticker_cmd(args=args)


if __name__ == "__main__":
    main()
