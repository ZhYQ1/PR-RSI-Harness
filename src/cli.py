from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from src.agent.pipeline import ReviewPipeline
from src.eval.runner import EvaluationHarness
from src.harness.runtime import HarnessRuntime
from src.memory.store import MemoryManager
from src.observability.metrics import start_metrics_server
from src.observability.trace import TraceReplayer
from src.storage.events import RedisStreamBus
from src.storage.factory import open_store

DEFAULT_DB = Path("data") / "harness.db"


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr-harness", description="PR risk control harness CLI")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite path or postgresql:// URL")
    sub = parser.add_subparsers(dest="command", required=True)

    submit = sub.add_parser("submit", help="submit a PR diff for review")
    submit.add_argument("--repo", required=True)
    submit.add_argument("--pr", type=int, required=True)
    submit.add_argument("--branch", default="main")
    submit.add_argument("--tenant", default="default")
    submit.add_argument("--diff", help="inline diff text")
    submit.add_argument("--diff-file", help="path to a file containing the diff")

    resume = sub.add_parser("resume", help="resume a task from its last checkpoint")
    resume.add_argument("--task-id", required=True)

    ev = sub.add_parser("eval", help="run the evaluation benchmark")
    ev.add_argument("--dataset", default=None, help="path to benchmark JSON")

    evolve = sub.add_parser("evolve", help="cluster failures, propose prompt candidates, apply gate")
    evolve.add_argument("--dataset", default=None, help="path to benchmark JSON")

    replay = sub.add_parser("replay", help="replay a task trace and attribute failures")
    replay.add_argument("--task-id", required=True)

    sub.add_parser("dead-letters", help="list dead letter queue entries")

    events = sub.add_parser("events", help="read events from the Redis Stream bus")
    events.add_argument(
        "--bus-url", default=os.environ.get("REDIS_URL", ""), help="redis:// URL (defaults to REDIS_URL)"
    )
    events.add_argument("--last-id", default="0")
    events.add_argument("--count", type=int, default=100)

    serve = sub.add_parser("serve", help="expose Prometheus metrics")
    serve.add_argument("--port", type=int, default=8000)

    return parser


def _read_diff(args: argparse.Namespace) -> str:
    if args.diff_file:
        return Path(args.diff_file).read_text(encoding="utf-8")
    if args.diff is not None:
        return args.diff
    raise SystemExit("provide --diff or --diff-file")


def _outcome_payload(runtime: HarnessRuntime, outcome) -> dict[str, Any]:
    task = outcome.task
    payload: dict[str, Any] = {
        "task_id": task.id,
        "status": task.status.value,
        "retry_count": task.retry_count,
    }
    if outcome.result is not None:
        payload["findings"] = outcome.result.findings
        payload["validated"] = outcome.result.validated
        payload["patch"] = outcome.result.patch
    if outcome.error:
        payload["error"] = outcome.error
    return payload


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = open_store(args.db)
    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        store.attach_bus(RedisStreamBus(redis_url))

    if args.command == "serve":
        start_metrics_server(port=args.port)
        print(f"metrics listening on :{args.port}", flush=True)
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            return 0

    if args.command == "events":
        if not args.bus_url:
            raise SystemExit("provide --bus-url or set REDIS_URL to read events")
        bus = RedisStreamBus(args.bus_url)
        _print([{"id": event_id, **event} for event_id, event in bus.read(args.last_id, args.count)])
        return 0

    if args.command == "submit":
        diff = _read_diff(args)
        runtime = HarnessRuntime(store)
        pipeline = ReviewPipeline(memory=MemoryManager(store))
        outcome = runtime.submit_and_run(
            repo=args.repo,
            pr_number=args.pr,
            branch=args.branch,
            diff=diff,
            pipeline=pipeline,
            tenant=args.tenant,
        )
        _print(_outcome_payload(runtime, outcome))
        return 0 if outcome.error is None else 1

    if args.command == "resume":
        runtime = HarnessRuntime(store)
        pipeline = ReviewPipeline(memory=MemoryManager(store))
        outcome = runtime.resume(args.task_id, pipeline)
        _print(_outcome_payload(runtime, outcome))
        return 0 if outcome.error is None else 1

    if args.command == "eval":
        harness = EvaluationHarness(store)
        if args.dataset:
            harness.dataset_path = Path(args.dataset)
        report = harness.run()
        _print(
            {
                "validation": vars(report.validation),
                "holdout": vars(report.holdout),
                "gate": {"passed": report.gate.passed, "reason": report.gate.reason},
                "cases": report.cases,
                "path": report.path,
            }
        )
        return 0 if report.gate.passed else 1

    if args.command == "evolve":
        from src.feedback.generator import CandidateGenerator

        harness = EvaluationHarness(store)
        if args.dataset:
            harness.dataset_path = Path(args.dataset)
        proposals = harness.rsi.evolve(CandidateGenerator(), evaluate=lambda: harness.run().validation)
        _print(proposals)
        return 0

    if args.command == "replay":
        replayer = TraceReplayer(store)
        attribution = replayer.attribute_failure(args.task_id)
        _print(
            {
                "task_id": args.task_id,
                "steps": [step.__dict__ for step in replayer.replay(args.task_id)],
                "failure_attribution": attribution.as_dict() if attribution else None,
            }
        )
        return 0

    if args.command == "dead-letters":
        _print(
            [
                {
                    "id": letter.id,
                    "task_id": letter.task_id,
                    "reason": letter.reason,
                    "detail": letter.detail,
                    "handling_status": letter.handling_status,
                    "handler": letter.handler,
                }
                for letter in store.list_dead_letters()
            ]
        )
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
