from __future__ import annotations

import csv
import time
from collections.abc import Callable
from pathlib import Path


class PlayerHistoryStore:
    """Append-only storage for player-count observations."""

    def __init__(
        self,
        path: str | Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.clock = clock

    def record(
        self,
        player_count: int,
        appid: str | None = None,
        timestamp: float | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not self.path.exists() or self.path.stat().st_size == 0

        with self.path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if write_header:
                writer.writerow(["timestamp", "appid", "player_count"])
            writer.writerow([
                f"{self.clock() if timestamp is None else timestamp:.3f}",
                appid or "",
                int(player_count),
            ])
