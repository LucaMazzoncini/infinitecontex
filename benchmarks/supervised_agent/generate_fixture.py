# ruff: noqa: E501
"""Generate an original deterministic async-scheduler benchmark fixture."""

from pathlib import Path


def generate(root: Path) -> dict[str, int]:
    visible = root / "visible"
    hidden = root / "evaluator"
    visible.mkdir(parents=True, exist_ok=True)
    hidden.mkdir(parents=True, exist_ok=True)
    files = {
        "scheduler/models.py": "from dataclasses import dataclass\n\n@dataclass\nclass Job:\n    name: str\n    payload: str\n",
        "scheduler/engine.py": "import asyncio\n\nasync def execute(job, handler):\n    return await handler(job)\n",
        "scheduler/config.py": "from dataclasses import dataclass\n\n@dataclass\nclass SchedulerConfig:\n    concurrency: int = 1\n",
        "scheduler/cli.py": "def status(job):\n    return f'{job.name}: ready'\n",
        "scheduler/queue.py": "class Queue:\n    def __init__(self): self.items=[]\n    def push(self,item): self.items.append(item)\n",
        "scheduler/events.py": "class EventLog:\n    def __init__(self): self.events=[]\n    def emit(self,event): self.events.append(event)\n",
        "scheduler/serialization.py": "def dump_job(job): return {'name': job.name, 'payload': job.payload}\n",
        "tests/test_engine.py": "import asyncio\nfrom scheduler.engine import execute\nfrom scheduler.models import Job\n\ndef test_execute():\n    async def ok(job): return job.payload\n    assert asyncio.run(execute(Job('x','ok'),ok)) == 'ok'\n",
        "tests/test_cli.py": "from scheduler.cli import status\nfrom scheduler.models import Job\n\ndef test_status(): assert status(Job('x','p')) == 'x: ready'\n",
        "docs/usage.md": "# Scheduler\n\nJobs execute once. Configuration is optional.\n",
        "README.md": "# Bounded Async Scheduler\n",
        "pyproject.toml": "[project]\nname='bounded-scheduler'\nversion='0.1.0'\n",
    }
    for index in range(10):
        files[f"scheduler/helpers_{index}.py"] = "def identity(value):\n    return value\n" * 150
    for name, content in files.items():
        path = visible / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (hidden / "test_retry_contract.py").write_text("# hidden evaluator: never model-visible\n", encoding="utf-8")
    return {"files": len(files), "lines": sum(x.count("\n") for x in files.values())}
