"""Small Rich terminal adapter for read-only chat."""

from __future__ import annotations

from rich.console import Console

from infinitecontex.chat.application import ChatApplication


def run_terminal(application: ChatApplication, console: Console) -> None:
    application.start()
    console.print("[bold]Infinite Context[/bold]")
    console.print(f"Repo: {application.project_root}")
    console.print(f"Model: {application.model} | mode: read-only")
    console.print("Type /help for commands.")
    while True:
        try:
            value = console.input("[bold cyan]> [/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        wrote_stream = False

        def emit(text: str) -> None:
            nonlocal wrote_stream
            console.print(text, end="")
            wrote_stream = True

        if not application.handle(value, emit):
            break
        if wrote_stream:
            console.print()
