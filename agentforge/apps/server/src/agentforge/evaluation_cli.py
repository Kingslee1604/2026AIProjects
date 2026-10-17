"""`agentforge eval run`: wires the evaluation harness to the configured services."""

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentforge.core.settings import Settings


def add_arguments(parser: argparse.ArgumentParser) -> None:
    actions = parser.add_subparsers(dest="eval_command", required=True)
    run = actions.add_parser("run", help="Measure retrieval and answers on a gold dataset")
    run.add_argument("--dataset", default="northwind")
    run.add_argument("--data", type=Path, default=Path("demo"), help="the repository's demo/")
    run.add_argument("--questions", choices=["all", "demo"], default="all")
    run.add_argument(
        "--chat-model",
        action="append",
        dest="chat_models",
        help="provider:model to answer with; repeat to compare (default: the configured one)",
    )
    run.add_argument("--retrieval-only", action="store_true", help="skip the answer step")
    run.add_argument("--out", type=Path, required=True, help="directory for report.json/.md")
    run.add_argument("--git-sha", default=None, help="commit being measured (for the manifest)")


def run(args: argparse.Namespace, settings: "Settings") -> int:
    # Imported here, so other `agentforge` commands start without loading the whole app.
    import asyncio
    import sys
    from datetime import UTC, datetime

    from agentforge.db.migrate import DEFAULT_ORG_ID
    from agentforge.evaluation import report
    from agentforge.evaluation.dataset import load_dataset
    from agentforge.evaluation.runner import EvalDeps, run_eval
    from agentforge.models import ModelGateway
    from agentforge.models.gateway import Recorder, build_gateway
    from agentforge.models.providers import FakeProvider
    from agentforge.qa.fake import with_grounded_answers
    from agentforge.services import build_services, qa_config

    started = datetime.now(UTC)
    dataset = load_dataset(args.data, args.dataset)
    chat_models: list[str] = (
        [] if args.retrieval_only else (args.chat_models or [settings.chat_model])
    )

    def make_gateway(recorder: Recorder) -> ModelGateway:
        gateway = build_gateway(settings, recorder)
        fake = gateway.provider("fake")
        if isinstance(fake, FakeProvider):  # fake mode answers from the evidence, as the app does
            fake.responder = with_grounded_answers(fake.responder)
        return gateway

    async def main() -> dict[str, object]:
        services = build_services(settings)
        try:
            deps = EvalDeps(
                sessions=services.sessions,
                knowledge=services.knowledge,
                retriever=services.retriever,
                gateway=services.gateway,
                agents=services.agents,
                embedding_model=settings.embedding_model,
                embedding_table=services.embedding_table,
                qa_config=qa_config(settings),
                make_gateway=make_gateway,
            )
            results = await run_eval(
                deps,
                dataset,
                org_id=DEFAULT_ORG_ID,
                which=args.questions,
                chat_models=chat_models,
                progress=lambda line: print(line, file=sys.stderr, flush=True),
            )
        finally:
            await services.close()
        config = deps.qa_config
        return {
            **results,
            "manifest": report.manifest(
                dataset=dataset.name,
                dataset_sha256=dataset.sha256,
                git_sha=args.git_sha,
                settings={
                    "embedding_model": settings.embedding_model,
                    "chat_models": ", ".join(chat_models) or "(retrieval only)",
                    "top_k": config.top_k,
                    "max_distance": config.max_distance,
                    "verifier_mode": config.verifier_mode,
                    "model_provider": settings.model_provider,
                },
                digests=report.model_digests(
                    settings.ollama_base_url, [settings.embedding_model, *chat_models]
                ),
                started=started,
            ),
        }

    results = asyncio.run(main())
    json_path, md_path = report.write(results, args.out)
    print(f"wrote {json_path} and {md_path}")
    return 0
