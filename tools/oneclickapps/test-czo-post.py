import time

import zulip
from rich.console import Console

console = Console()


def refresh_connection() -> zulip.Client:
    console.log("Making new client")
    return zulip.Client()


def bisect(low: float, high: float) -> float:
    if (high - low) < 1.0:
        return high
    attempted = (high + low) / 2
    c = refresh_connection()
    try:
        console.log(f"Attempting {attempted} seconds of sleep...")
        time.sleep(attempted)
        response = c.get_server_settings()
        assert response["result"] == "success"
        console.log("[green]Success!")
        return bisect(attempted, high)
    except Exception:
        console.log("[red]Failed!")
        return bisect(low, attempted)


print(bisect(150.0, 300.0))
