import os
import time
import logging
from pathlib import Path
from typing import Optional, List, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Default cache quota: 4096 MB (4 GB)
DEFAULT_MAX_CACHE_MB = 4096


class LRUStorageManager:
    """
    Manages disk-based model caching with a hard size cap (default 4 GB)
    and Least Recently Used (LRU) eviction policy.
    """

    def __init__(
        self,
        output_dir: Path,
        input_dir: Path,
        max_cache_mb: Optional[int] = None,
    ) -> None:
        self.output_dir = output_dir
        self.input_dir = input_dir

        env_cap = os.environ.get("MAX_CACHE_MB")
        if max_cache_mb is not None:
            self.max_bytes = max_cache_mb * 1024 * 1024
        elif env_cap and env_cap.strip().isdigit():
            self.max_bytes = int(env_cap.strip()) * 1024 * 1024
        else:
            self.max_bytes = DEFAULT_MAX_CACHE_MB * 1024 * 1024

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.input_dir.mkdir(parents=True, exist_ok=True)

    @property
    def max_mb(self) -> float:
        return self.max_bytes / (1024 * 1024)

    def get_current_cache_size(self) -> int:
        """Calculate total size in bytes of all files in output_dir."""
        total = 0
        if not self.output_dir.exists():
            return 0
        for f in self.output_dir.iterdir():
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def is_cached(self, filename: str) -> bool:
        """Check whether a model file exists on disk in the output cache."""
        path = self.output_dir / filename
        return path.is_file()

    def touch(self, filename: str) -> None:
        """Update last accessed time (atime and mtime) on disk."""
        path = self.output_dir / filename
        if path.is_file():
            try:
                os.utime(path, None)
            except OSError as e:
                logger.warning(f"[lru] Failed to update atime for {filename}: {e}")

    async def touch_async(self, filename: str, db=None) -> None:
        """Touch file on disk and refresh last_accessed_at in MongoDB."""
        self.touch(filename)
        if db is not None:
            try:
                now = datetime.now(timezone.utc).isoformat()
                await db.searches.update_many(
                    {
                        "$or": [
                            {"primary_model.url": {"$regex": filename}},
                            {"all_models.url": {"$regex": filename}},
                        ]
                    },
                    {"$set": {"last_accessed_at": now, "is_cached": True}},
                )
            except Exception as e:
                logger.debug(f"[lru] DB touch skipped/failed for {filename}: {e}")

    async def evict_if_needed(self, incoming_bytes: int = 0, db=None) -> int:
        """
        Evicts Least Recently Used (oldest atime) files if current_size + incoming_bytes > max_bytes.
        Returns total bytes freed.
        """
        current_size = self.get_current_cache_size()
        target_size = self.max_bytes

        if current_size + incoming_bytes <= target_size:
            return 0

        logger.info(
            f"[lru] Cache cap exceeded: {current_size / (1024 * 1024):.1f} MB + "
            f"{incoming_bytes / (1024 * 1024):.1f} MB > {self.max_mb:.0f} MB limit. Running LRU eviction..."
        )

        # Collect files sorted by access time (st_atime) ascending (oldest accessed first)
        files: List[Tuple[Path, float, int]] = []
        for f in self.output_dir.iterdir():
            if f.is_file() and f.suffix.lower() == ".glb":
                try:
                    st = f.stat()
                    last_access = max(st.st_atime, st.st_mtime)
                    files.append((f, last_access, st.st_size))
                except OSError:
                    continue

        files.sort(key=lambda x: x[1])  # Oldest accessed first

        freed_bytes = 0
        for file_path, _, size in files:
            if (current_size - freed_bytes + incoming_bytes) <= target_size:
                break
            try:
                filename = file_path.name
                file_path.unlink(missing_ok=True)
                freed_bytes += size
                logger.info(f"[lru] Evicted {filename} ({size / (1024 * 1024):.2f} MB)")

                # Mark as uncached in MongoDB
                if db is not None:
                    try:
                        await db.searches.update_many(
                            {
                                "$or": [
                                    {"primary_model.url": {"$regex": filename}},
                                    {"all_models.url": {"$regex": filename}},
                                ]
                            },
                            {"$set": {"is_cached": False}},
                        )
                    except Exception as exc:
                        logger.debug(f"[lru] DB mark uncached skipped for {filename}: {exc}")
            except OSError as e:
                logger.error(f"[lru] Failed to delete {file_path}: {e}")

        logger.info(
            f"[lru] Eviction complete: freed {freed_bytes / (1024 * 1024):.2f} MB. "
            f"New cache size: {(current_size - freed_bytes) / (1024 * 1024):.1f} MB."
        )
        return freed_bytes

    def cleanup_temp_inputs(self, max_age_hours: float = 1.0) -> int:
        """
        Cleans temporary files from input/ older than max_age_hours.
        Files in input/ are temporary segmentation buffers only.
        """
        if not self.input_dir.exists():
            return 0
        cutoff = time.time() - (max_age_hours * 3600)
        deleted = 0
        for f in self.input_dir.iterdir():
            if f.is_file():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                        deleted += 1
                except OSError:
                    pass
        return deleted
