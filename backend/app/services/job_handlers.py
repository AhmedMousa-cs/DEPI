"""Job handlers. Importing this module registers them. Real agent jobs arrive in M4+."""
import time

from app.services.jobs import JobContext, register


@register("demo.echo")
def demo_echo(ctx: JobContext, params: dict) -> str:
    """Smoke-test handler (kept for M3 tests; remove or disable in M9)."""
    steps = max(1, min(int(params.get("steps", 3)), 20))
    delay = max(0.0, min(float(params.get("delay", 0)), 5.0))
    if params.get("fail"):
        raise RuntimeError("demo failure requested")
    for i in range(steps):
        ctx.check_deadline()
        time.sleep(delay)
        ctx.heartbeat(progress=(i + 1) * 100 / steps)
    return f"echo:{params.get('message', '')}"[:255]
