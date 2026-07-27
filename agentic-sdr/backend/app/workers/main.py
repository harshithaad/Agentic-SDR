"""Entry point: python -m app.workers.main <stage>
One container image, five roles — the deployment picks the stage by argv."""
import argparse

from app.workers.runner import STAGES, run_worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic SDR stage worker")
    parser.add_argument("stage", choices=sorted(STAGES.keys()))
    args = parser.parse_args()
    run_worker(args.stage)


if __name__ == "__main__":
    main()
