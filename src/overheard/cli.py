import argparse
import sys


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


def main(argv: list[str] | None = None) -> int:
    """Run the `overheard` command.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.

    Returns:
        The process exit code.
    """
    args = build_parser().parse_args(argv)
    print(f"overheard {args.command}: not built yet", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
