from __future__ import annotations

from pathlib import Path

PATCH_NAME = Path(__file__).stem


def run(benches_root: Path) -> None:
    """Install the daily certificate renewal timer on hosts that already issue Let's Encrypt
    certificates. The timer is host-wide, so the first bench that needs it installs it."""
    from pilot.config import BenchConfig
    from pilot.internal.patch_state import is_applied, mark_applied
    from pilot.managers.letsencrypt import install_renewal_timer
    from pilot.managers.platform import is_linux

    for bench_dir in sorted(benches_root.glob("*")):
        if not bench_dir.is_dir() or not (bench_dir / "bench.toml").exists():
            continue
        if is_applied(bench_dir, PATCH_NAME):
            continue
        config = BenchConfig.read(bench_dir)
        if is_linux() and config.production.enabled and config.letsencrypt.email and install_renewal_timer():
            print("Installed the certificate renewal timer.")
        mark_applied(bench_dir, PATCH_NAME)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from pilot.utils import benches_dir

    run(benches_dir())
