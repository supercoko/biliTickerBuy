from argparse import Namespace
import os
import sys

from util import GlobalStatusInstance


def _set_terminal_title(title: str) -> None:
    """在当前终端窗口标题栏显示配置名，便于多并发时区分。"""
    try:
        if os.name == "nt":
            os.system(f'title {title}')
        else:
            sys.stdout.write(f"\33]0;{title}\a")
            sys.stdout.flush()
    except Exception:
        pass


def buy_cmd(args: Namespace):
    from util.LogConfig import loguru_config
    import uuid

    from util import LOG_DIR
    from task.buy import buy
    from loguru import logger

    def load_tickets_info(tickets_info: str) -> tuple[str, str | None]:
        config_path = os.path.expanduser(tickets_info)
        if os.path.isfile(config_path):
            logger.info(f"使用配置文件：{config_path}")
            try:
                with open(config_path, "r", encoding="utf-8") as config_file:
                    return config_file.read(), config_path
            except OSError as exc:
                raise SystemExit(f"读取配置文件失败: {exc}") from exc
        return tickets_info, None

    tickets_info, config_path = load_tickets_info(args.tickets_info)
    filename = os.path.basename(config_path) if config_path else "default"
    filename_only = os.path.basename(filename)
    GlobalStatusInstance.nowTask = filename_only
    if getattr(args, "web", False):
        log_file = loguru_config(LOG_DIR, f"{uuid.uuid1()}.log", enable_console=False)
        from task.endpoint import start_heartbeat_thread
        import gradio_client
        import gradio as gr
        from gradio_log import Log

        with gr.Blocks(
            head="""<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>""",
            title=f"{filename_only}",
            fill_height=True,
        ) as demo:
            gr.Markdown(
                f"""
                # 当前抢票 {filename_only}
                > 你可以在这里查看程序的运行日志
                """
            )

            Log(
                log_file,
                dark=True,
                scale=1,
                xterm_log_level="info",
                xterm_scrollback=5000,
                elem_classes="h-full",
            )

            def exit_program():
                print(f"{filename_only} ，关闭程序...")
                os._exit(0)

            btn = gr.Button("关闭程序")
            btn.click(fn=exit_program)

        print(f"抢票日志路径： {log_file}")
        print(f"运行程序网址   ↓↓↓↓↓↓↓↓↓↓↓↓↓↓   {filename_only} ")
        is_docker = os.path.exists("/.dockerenv") or os.environ.get("BTB_DOCKER") == "1"
        demo.launch(
            server_name=args.server_name,
            server_port=args.port,
            share=args.share or is_docker,
            inbrowser=not is_docker,
            prevent_thread_lock=True,
        )
        client = gradio_client.Client(args.endpoint_url)
        assert demo.local_url
        start_heartbeat_thread(
            client,
            self_url=demo.local_url,
            to_url=args.endpoint_url,
        )
    else:
        log_file = loguru_config(LOG_DIR, f"{uuid.uuid1()}.log", enable_console=True)

    # 在终端窗口标题与日志开头突出显示「当前配置」，多并发时一眼可辨
    _set_terminal_title(f"抢票 - {filename_only}")
    logger.info(f"📂 当前抢票配置: {filename_only}")

    buy(
        tickets_info,
        args.time_start,
        args.interval,
        args.audio_path,
        args.pushplusToken,
        args.serverchanKey,
        args.barkToken,
        args.https_proxys,
        args.serverchan3ApiUrl,
        args.ntfy_url,
        args.ntfy_username,
        args.ntfy_password,
        getattr(args, "feishu_webhook", None) or None,
        getattr(args, "feishu_secret", None) or None,
        not args.hide_random_message,
        True,
        max_retries=getattr(args, "max_retries", 200),
        interval_jitter=getattr(args, "interval_jitter", 0.25),
        scavenge_mode=getattr(args, "scavenge_mode", False),
        scavenge_interval=getattr(args, "scavenge_interval", 3000),
        scavenge_max_retries=getattr(args, "scavenge_max_retries", 0),
    )
    logger.info("抢票完成后退出程序。。。。。")
