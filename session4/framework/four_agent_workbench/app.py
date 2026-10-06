from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from pathlib import Path

from .config import secret_values
from .domain import WorkbenchError
from .security import Redactor, WorkspaceLock


def arguments(argv=None):
    parser = argparse.ArgumentParser(description="Local four-agent software development workbench")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use scripted fixture models; no API key or model requests",
    )
    parser.add_argument(
        "--real-tests", action="store_true", help="Use actual Docker tests with --mock"
    )
    parser.add_argument(
        "--scenario",
        choices=["success", "test_failure", "provider_error", "slow"],
        default="success",
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run cycles without opening the desktop UI"
    )
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument(
        "--check", action="store_true", help="Validate configuration without making a model request"
    )
    return parser.parse_args(argv)


async def headless(controller, args):
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, controller.request_stop)
        loop.add_signal_handler(signal.SIGTERM, controller.request_stop)
    except NotImplementedError:
        signal.signal(signal.SIGINT, lambda *_: controller.request_stop())
    result = await controller.run("continuous" if args.continuous else "finite", args.cycles)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "completed" else 2


def main(argv=None):
    args = arguments(argv)
    if args.cycles < 1:
        raise SystemExit("--cycles must be a positive integer")
    # Set before importing LangGraph; no traces should leave this local application.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    from .orchestration import RunController

    root = args.workspace.resolve()
    lock = WorkspaceLock(root)
    controller = None
    background = None
    try:
        lock.acquire()
        controller = RunController(
            root, mock=args.mock, real_tests=args.real_tests, scenario=args.scenario
        )
        if args.check:
            reason = controller.validate_start()
            print(reason or "Configuration and prompts are valid. No model request was made.")
            return 2 if reason else 0
        if args.headless:
            return asyncio.run(headless(controller, args))
        from PySide6.QtWidgets import QApplication

        from .orchestration.controller import BackgroundLoop
        from .ui.window import MainWindow

        application = QApplication.instance() or QApplication([])
        application.setApplicationName("Four-Agent Workbench")
        background = BackgroundLoop()
        window = MainWindow(controller, background)
        window.show()
        return application.exec()
    except (WorkbenchError, OSError, ValueError) as exc:
        redactor = controller.redactor if controller else Redactor(secret_values(root))
        message = redactor.text(str(exc))
        if args.headless or args.check:
            print(message)
        else:
            from PySide6.QtWidgets import QApplication, QMessageBox

            application = QApplication.instance() or QApplication([])
            QMessageBox.critical(None, "Workbench cannot start", message)
        return 2
    finally:
        if background:
            background.close()
        if controller:
            controller.close()
        lock.close()
