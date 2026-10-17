import sys

from overheard.cli import _Children, ollama_line_shown

GIN_OK = '[GIN] 2026/10/18 - 01:10:05 | 200 |    9.75µs |  127.0.0.1 | GET      "/api/tags"'
GIN_404 = '[GIN] 2026/10/18 - 01:10:06 | 404 |   51.2µs |  127.0.0.1 | POST     "/api/show"'
GIN_500 = '[GIN] 2026/10/18 - 01:10:07 | 500 |  2.012s |  127.0.0.1 | POST     "/api/chat"'
INFO = 'time=2026-10-18T01:10:05.123+05:30 level=INFO source=routes.go:1 msg="Listening"'
WARN = 'time=2026-10-18T01:10:05.123+05:30 level=WARN source=sched.go:1 msg="gpu memory low"'
ERROR = 'time=2026-10-18T01:10:05.123+05:30 level=ERROR source=server.go:1 msg="runner died"'
BIND = "Error: listen tcp 127.0.0.1:11434: bind: address already in use"


def test_only_ollamas_warnings_and_errors_are_shown():
    assert not ollama_line_shown(GIN_OK)
    assert not ollama_line_shown(GIN_404)
    assert not ollama_line_shown(INFO)
    assert not ollama_line_shown("llama_model_loader: - kv   0: general.architecture str")
    assert ollama_line_shown(WARN)
    assert ollama_line_shown(ERROR)
    assert ollama_line_shown(GIN_500)
    assert ollama_line_shown(BIND)


def test_start_relays_a_childs_lines_through_its_filter(capsys):
    children = _Children()
    lines = [GIN_OK, INFO, WARN, GIN_OK, ERROR]
    code = "import sys; sys.stdout.write(sys.argv[1])"
    proc = children.spawn(
        "ollama", [sys.executable, "-c", code, "\n".join(lines) + "\n"], keep=ollama_line_shown
    )
    proc.wait(timeout=10)
    children.join_output(timeout=5)
    out = capsys.readouterr().out.splitlines()
    assert out == [f"[ollama] {WARN}", f"[ollama] {ERROR}"]
