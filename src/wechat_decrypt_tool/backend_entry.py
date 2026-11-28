"""Module entry point for source launches and backend settings restarts."""

import multiprocessing
import sys

def main() -> None:
    if sys.argv[1:2] == ["--wxgf-decode-worker"]:
        from wechat_decrypt_tool.wxgf_codec import worker_main

        worker_main(sys.argv[2:])
        return

    if sys.argv[1:2] == ["--wait-and-spawn"]:
        from wechat_decrypt_tool.server_lifecycle import run_delayed_restart_launcher

        sys.exit(run_delayed_restart_launcher(sys.argv[2:]))

    from wechat_decrypt_tool.desktop_parent_watchdog import (
        start_desktop_parent_watchdog_from_env,
    )
    from wechat_decrypt_tool.runtime_settings import (
        default_backend_host,
        read_effective_backend_host,
        read_effective_backend_port,
    )
    from wechat_decrypt_tool.server_lifecycle import run_uvicorn_server

    if "--smoke-backend" in sys.argv[1:]:
        import json
        from wechat_decrypt_tool.api import app

        payload = {
            "ok": True,
            "frozen": bool(getattr(sys, "frozen", False)),
            "platform": sys.platform,
            "title": app.title,
        }
        print(json.dumps(payload, ensure_ascii=True))
        return

    start_desktop_parent_watchdog_from_env()
    from wechat_decrypt_tool.api import app

    host, _ = read_effective_backend_host(default=default_backend_host())
    port, _ = read_effective_backend_port(default=10392)
    # Keep the application's file handlers and redaction filters.
    run_uvicorn_server(app, host=host, port=port, log_level="info", log_config=None)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
