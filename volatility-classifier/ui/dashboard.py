from rich.console import Console
from rich.table import Table

console = Console()


def render(rows: list[dict]) -> None:
    if not rows:
        console.print("[dim]no data[/dim]")
        return
    table = Table(*rows[0].keys())
    for row in rows:
        table.add_row(*(str(v) for v in row.values()))
    console.print(table)
