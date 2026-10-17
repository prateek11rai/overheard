import argparse
import asyncio
import contextlib
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

from overheard.settings import Settings, TemporalNotFoundError, get_settings, temporal_binary

OK, BAD, LATER = "✓", "✗", "·"
_LEVEL = re.compile(r"\blevel=(WARN|ERROR)\b")
_GIN_STATUS = re.compile(r"^\[GIN\][^|]*\|\s*(\d{3})\s*\|")


def build_parser() -> argparse.ArgumentParser:
    """Build the `overheard` argument parser.

    Returns:
        The parser with one sub-command per job.
    """
    parser = argparse.ArgumentParser(
        prog="overheard",
        description="Every morning, what people said about the problem your product solves.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    sub.add_parser("setup", help="create .data/, run database migrations, check tools")
    sub.add_parser("doctor", help="show ✓/✗ for every piece (never a secret)")
    sub.add_parser("start", help="start Temporal, the worker and the web app; Ctrl-C stops all")
    sub.add_parser("web", help="run the web app")
    sub.add_parser("worker", help="run the Temporal worker")
    demo = sub.add_parser("demo", help="add or remove demo data")
    demo.add_argument("action", choices=["load", "clear"])
    sub.add_parser("seed", help="add the sample products Mealwise and Shipnote, with no posts")
    brief = sub.add_parser("brief", help="run the morning job now and wait")
    brief.add_argument("--reddit", choices=["on", "off"], default="on")
    ev = sub.add_parser("eval", help="compare models on the checked posts")
    ev.add_argument("--model", action="append", required=True, help="a chat model (repeat)")
    ev.add_argument("--markdown", action="store_true", help="print the table as Markdown")
    ev.add_argument("--synthetic-only", action="store_true", help="only the synthetic items")
    ev.add_argument("--limit", type=int, default=None, help="use at most this many items")
    sub.add_parser("email-test", help="send a test digest to your own mailbox")
    return parser


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _host_port(address: str, default_port: int) -> tuple[str, int]:
    if "://" in address:
        parts = urlsplit(address)
        return parts.hostname or "127.0.0.1", parts.port or default_port
    host, _, port = address.rpartition(":")
    return host or "127.0.0.1", int(port or default_port)


def _temporal_bin(settings: Settings) -> tuple[str | None, str]:
    try:
        return str(temporal_binary(settings.overheard_temporal_bin)), ""
    except TemporalNotFoundError as exc:
        return None, str(exc)


def _line(mark: str, name: str, detail: str = "") -> None:
    print(f"  {mark} {name:<16} {detail}".rstrip())


async def _setup(settings: Settings) -> int:
    from overheard.ai import AI, role_models
    from overheard.db import Database

    settings.overheard_data_dir.mkdir(parents=True, exist_ok=True)
    settings.digests_dir.mkdir(parents=True, exist_ok=True)
    _line(OK, "data folder", str(settings.overheard_data_dir))
    db = Database(settings.dsn, settings.overheard_db_schema)
    try:
        applied = await db.migrate()
    except Exception as exc:
        _line(BAD, "Postgres", f"{type(exc).__name__}: check DATABASE_URL and that it's running")
        return 1
    _line(OK, "Postgres", f"schema {settings.overheard_db_schema} · {len(applied)} new migrations")
    async with db:
        models = role_models(
            await db.get_setting("models"), settings.chat_model, settings.embed_model
        )
        key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        async with AI(settings.llm_base_url, models, key) as ai:
            found = await ai.discover()
        if not found.reachable:
            _line(BAD, "model server", f"no answer at {settings.llm_base_url} (start ollama)")
        else:
            for role, kind in (("label", "chat"), ("embed", "embedding")):
                info = found.find(models[role])
                if info is None:
                    _line(
                        BAD, f"{kind} model", f"{models[role]} missing: ollama pull {models[role]}"
                    )
                else:
                    _line(OK, f"{kind} model", models[role])
                    if kind == "embedding" and info.dim:
                        await db.ensure_embedding_index(models[role], info.dim)
    tbin, why = _temporal_bin(settings)
    _line(OK if tbin else BAD, "temporal", tbin or why)
    from overheard import workflows

    try:
        client = await asyncio.wait_for(workflows.connect(settings), timeout=5)
        async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
            result = await workflows.ensure_schedule(client, db, settings.default_timezone())
        _line(OK, "schedule", f"{workflows.SCHEDULE_ID} {result}")
    except Exception:
        _line(LATER, "schedule", "Temporal isn't running; the worker creates it when it starts")
    return 0


async def _doctor(settings: Settings) -> int:
    from overheard.ai import AI, role_models
    from overheard.db import Database

    print("overheard doctor")
    failures = 0
    models: dict = {}
    try:
        async with Database(settings.dsn, settings.overheard_db_schema, max_size=2) as db:
            health = await db.health()
            models = role_models(
                await db.get_setting("models"), settings.chat_model, settings.embed_model
            )
        if health["tables"] < 5:
            _line(BAD, "Postgres", "not set up: run uv run overheard setup")
            failures += 1
        else:
            _line(OK, "Postgres", f"pgvector {health['pgvector']} · schema ready")
    except Exception as exc:
        _line(BAD, "Postgres", f"{type(exc).__name__}: check DATABASE_URL")
        failures += 1
        models = role_models(None, settings.chat_model, settings.embed_model)

    key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    async with AI(settings.llm_base_url, models, key) as ai:
        found = await ai.discover()
    if not found.reachable:
        _line(BAD, "Ollama", f"no answer at {settings.llm_base_url}: run ollama serve")
        failures += 1
    else:
        missing = [m for m in (models["label"], models["embed"]) if found.find(m) is None]
        if missing:
            _line(BAD, "Ollama", "missing " + ", ".join(missing) + " (ollama pull <model>)")
            failures += 1
        else:
            _line(OK, "Ollama", f"{models['label']} · {models['embed']}")

    from overheard import workflows

    status = await workflows.temporal_status(settings)
    if status["temporal"]:
        _line(OK, "Temporal", settings.temporal_address)
    else:
        _line(BAD, "Temporal", f"no answer at {settings.temporal_address}: uv run overheard start")
        failures += 1
    if status["worker"]:
        _line(OK, "worker", f"polling {workflows.TASK_QUEUE}")
    else:
        _line(BAD, "worker", "not running: uv run overheard start (or overheard worker)")
        failures += 1
    if status.get("schedule"):
        _line(OK, "schedule", status["schedule"])

    if settings.serpapi_api_key is None:
        _line(LATER, "SerpApi", "not set yet (Hacker News only)")
    else:
        _line(OK, "SerpApi", "key set")
    _line(
        OK,
        "Hacker News",
        "free, no key needed" + (" · offline" if settings.overheard_offline else ""),
    )
    if settings.email_mode == "file":
        _line(OK, "email", f"file mode · {settings.digests_dir}")
    elif settings.smtp_host:
        _line(OK, "SMTP", "set (uv run overheard email-test to try it)")
    else:
        _line(LATER, "SMTP", "not set yet (digests stay in the app)")
    return 1 if failures else 0


def ollama_line_shown(line: str) -> bool:
    """Whether a line from `ollama serve` belongs in `start`'s output: warnings and errors only.

    Args:
        line: One line of Ollama's output.

    Returns:
        True for `level=WARN`/`level=ERROR` lines, 5xx request lines and fatal errors.
    """
    if _LEVEL.search(line):
        return True
    gin = _GIN_STATUS.match(line)
    if gin:
        return int(gin.group(1)) >= 500
    return line.lstrip().lower().startswith(("error", "panic", "fatal"))


class _Children:
    def __init__(self) -> None:
        self.procs: list[tuple[str, subprocess.Popen]] = []
        self.pumps: list[threading.Thread] = []

    def spawn(
        self,
        name: str,
        cmd: list[str],
        env: dict | None = None,
        keep: Callable[[str], bool] | None = None,
    ) -> subprocess.Popen:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            start_new_session=True,
        )
        pump = threading.Thread(target=self._pump, args=(name, proc, keep), daemon=True)
        pump.start()
        self.pumps.append(pump)
        self.procs.append((name, proc))
        return proc

    @staticmethod
    def _pump(name: str, proc: subprocess.Popen, keep: Callable[[str], bool] | None) -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            if keep is None or keep(line):
                print(f"[{name}] {line.rstrip()}", flush=True)

    def join_output(self, timeout: float) -> None:
        for pump in self.pumps:
            pump.join(timeout)

    def stop(self) -> None:
        for _, proc in reversed(self.procs):
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 10
        for _, proc in reversed(self.procs):
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()


def _wait_port(host: str, port: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _port_open(host, port):
            return True
        time.sleep(0.3)
    return False


def _start(settings: Settings) -> int:
    children = _Children()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        llm_host, llm_port = _host_port(settings.llm_base_url, 11434)
        if not _port_open(llm_host, llm_port):
            if settings.is_default_ollama and shutil.which("ollama"):
                print("Starting ollama serve")
                ollama = [shutil.which("ollama") or "ollama", "serve"]
                children.spawn("ollama", ollama, env, keep=ollama_line_shown)
                if not _wait_port(llm_host, llm_port, 20):
                    print("Ollama didn't start. Run `ollama serve` and try again.")
                    return 1
            else:
                print(f"No model server at {settings.llm_base_url}. Start it and try again.")
                return 1

        t_host, t_port = _host_port(settings.temporal_address, 7233)
        if not _port_open(t_host, t_port):
            if t_host not in ("127.0.0.1", "localhost"):
                print(f"No Temporal at {settings.temporal_address}. Start it or fix the address.")
                return 1
            tbin, why = _temporal_bin(settings)
            if tbin is None:
                print(why)
                return 1
            _, ui_port = _host_port(settings.temporal_ui_url, 8233)
            settings.overheard_data_dir.mkdir(parents=True, exist_ok=True)
            db_file = str(settings.overheard_data_dir / "temporal.db")
            cmd = [tbin, "server", "start-dev", "--ip", "127.0.0.1", "--port", str(t_port)]
            cmd += ["--ui-port", str(ui_port), "--db-filename", db_file, "--log-level", "warn"]
            children.spawn("temporal", cmd, env)
            if not _wait_port(t_host, t_port, 30):
                print("Temporal didn't start.")
                return 1

        if asyncio.run(_setup_quiet(settings)) != 0:
            return 1
        me = [sys.executable, "-m", "overheard.cli"]
        children.spawn("worker", [*me, "worker"], env)
        children.spawn("web", [*me, "web"], env)

        from overheard import workflows

        asyncio.run(workflows.sync_schedule_from_db(settings))
        _, web_port = settings.overheard_host, settings.overheard_port
        if not _wait_port("127.0.0.1", web_port, 20):
            print("The web app didn't start; see [web] above.")
        print(f"\nOpen {settings.app_url}")
        print(f"Temporal UI {settings.temporal_ui_url}\nCtrl-C stops everything.\n", flush=True)
        while all(p.poll() is None for _, p in children.procs):
            time.sleep(0.5)
        for name, p in children.procs:
            if p.poll() is not None:
                print(f"{name} stopped (exit {p.returncode}); stopping the rest.")
        return 1
    except KeyboardInterrupt:
        print("\nStopping…")
        return 0
    finally:
        children.stop()


async def _setup_quiet(settings: Settings) -> int:
    from overheard.db import Database

    settings.overheard_data_dir.mkdir(parents=True, exist_ok=True)
    try:
        await Database(settings.dsn, settings.overheard_db_schema).migrate()
    except Exception as exc:
        print(f"Postgres: {type(exc).__name__}. Check DATABASE_URL, then uv run overheard setup.")
        return 1
    return 0


def _web(settings: Settings) -> int:
    import uvicorn

    from overheard.web import create_app

    if not settings.is_local_host and settings.overheard_password is None:
        print(
            f"Refusing to listen on {settings.overheard_host}: set OVERHEARD_PASSWORD first, "
            "or use 127.0.0.1."
        )
        return 2
    uvicorn.run(
        create_app(settings),
        host=settings.overheard_host,
        port=settings.overheard_port,
        log_level="warning",
        server_header=False,
        proxy_headers=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the `overheard` command.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    settings = get_settings()
    cmd = args.command
    if cmd == "setup":
        return asyncio.run(_setup(settings))
    if cmd == "doctor":
        return asyncio.run(_doctor(settings))
    if cmd == "start":
        return _start(settings)
    if cmd == "web":
        return _web(settings)
    if cmd == "worker":
        from overheard import workflows

        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(workflows.run_worker(settings))
        return 0
    if cmd == "demo":
        from overheard import demo

        return asyncio.run(demo.main(settings, args.action))
    if cmd == "seed":
        from overheard import demo

        return asyncio.run(demo.seed(settings))
    if cmd == "brief":
        from overheard import workflows

        return asyncio.run(workflows.run_brief_now(settings, reddit=args.reddit == "on"))
    if cmd == "eval":
        from overheard import evals

        return asyncio.run(
            evals.main(
                settings,
                args.model,
                markdown=args.markdown,
                synthetic_only=args.synthetic_only,
                limit=args.limit,
            )
        )
    if cmd == "email-test":
        from overheard import mail

        return asyncio.run(mail.email_test(settings))
    return 2


if __name__ == "__main__":
    sys.exit(main())
