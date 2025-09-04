from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import re
import difflib


@dataclass
class DiffFile:
    path: str
    status: str  # added | modified | deleted
    patch: str
    size: int
    truncated: bool = False


class DiffService:
    """
    backup_then_write によって同一ディレクトリに作られた
    {YYYYMMDDHHMMSS}bk_<filename> を基に、直近のバックアップと現行ファイルの差分を生成する。
    """

    BK_PATTERN = re.compile(r"^(?P<ts>\d{14})bk_(?P<name>.+)$")

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = (base_dir or Path.cwd()).resolve()

    def _iter_backups(self) -> List[Tuple[Path, str, str]]:
        """
        すべてのバックアップファイルを探索し、(backup_path, ts, original_name) を返す。
        """
        out: List[Tuple[Path, str, str]] = []
        for p in self.base_dir.rglob("*"):
            if not p.is_file():
                continue
            m = self.BK_PATTERN.match(p.name)
            if not m:
                continue
            ts = m.group("ts")
            name = m.group("name")
            out.append((p, ts, name))
        return out

    def _latest_backup_per_file(self, limit: int = 100) -> List[Tuple[Path, Path]]:
        """
        各元ファイルに対して最新バックアップを1件選び、(backup_path, original_path) のリストを返す。
        最新は ts の降順で判定。
        """
        candidates = self._iter_backups()
        # original -> (ts, backup_path)
        latest: Dict[str, Tuple[str, Path, Path]] = {}
        for bk_path, ts, name in candidates:
            orig_path = bk_path.parent / name
            key = str(orig_path.resolve())
            prev = latest.get(key)
            if (prev is None) or (ts > prev[0]):  # ts は文字列比較でOK（YYYYMMDDHHMMSS）
                latest[key] = (ts, bk_path, orig_path)
        # ts 降順でソート
        pairs = sorted(latest.values(), key=lambda x: x[0], reverse=True)
        out: List[Tuple[Path, Path]] = [(bk, orig) for _, bk, orig in pairs[:limit]]
        return out

    def _is_text(self, data: bytes) -> bool:
        try:
            data.decode("utf-8")
            return True
        except Exception:
            return False

    def _read_text_safe(self, p: Path, max_bytes: int = 500_000) -> Tuple[str, int, bool]:
        data = p.read_bytes()
        size = len(data)
        truncated = False
        if not self._is_text(data):
            # バイナリは空文字扱い（差分スキップ）
            return "", size, False
        if size > max_bytes:
            data = data[:max_bytes]
            truncated = True
        return data.decode("utf-8", errors="ignore"), size, truncated

    def latest_diffs(self, limit_files: int = 50) -> List[DiffFile]:
        pairs = self._latest_backup_per_file(limit=limit_files)
        results: List[DiffFile] = []
        for bk_path, orig_path in pairs:
            if not orig_path.exists():
                # 削除扱い（元ファイルが無い）
                old_text, old_size, old_trunc = self._read_text_safe(bk_path)
                new_text = ""
                status = "deleted"
            else:
                old_text, old_size, old_trunc = self._read_text_safe(bk_path)
                new_text, new_size, new_trunc = self._read_text_safe(orig_path)
                if old_text == "" and old_size > 0 and not self._is_text(bk_path.read_bytes()):
                    # バイナリはスキップ
                    continue
                if old_text == "" and new_text != "":
                    status = "added"
                elif old_text != "" and new_text == "":
                    status = "deleted"
                else:
                    status = "modified"

            rel = str(orig_path.relative_to(self.base_dir)) if orig_path.exists() else str((bk_path.parent / bk_path.name[17:]).relative_to(self.base_dir))

            diff_lines = list(difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                lineterm=""
            ))
            patch = "\n".join(diff_lines)
            results.append(DiffFile(
                path=rel,
                status=status,
                patch=patch,
                size=(bk_path.stat().st_size if bk_path.exists() else 0) + (orig_path.stat().st_size if orig_path.exists() else 0),
                truncated=False,
            ))
        return results
