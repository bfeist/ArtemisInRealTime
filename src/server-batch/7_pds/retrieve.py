"""One-shot orchestration that resumes a saved discovery and writes final coverage."""
from .pipeline import run_stage


if __name__ == "__main__":
    run_stage("retrieve")
