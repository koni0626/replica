from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import re
import difflib
import subprocess


@dataclass
class DiffFile:
    path: str
    status: str  # added | modified | deleted | renamed | untracked
    patch: str
    size: int
    truncated: bool = False


class DiffService:
    """
    追加: Git 管理下の doc_path であれば、git diff ベースの差分も提供する。
    """

    BK_PATTERN = re.compile(r"^(?P<ts>\d{14})bk_(?P<name>.+)$")

    def __init__(self, base_dir: Optional[Path] = None, project_id: Optional[int] = None):
        """
        base_dir が明示されていない場合は project_id を用いて Projects.doc_path から探索ルートを解決する。
        doc_path 未設定/不正の場合は ValueError を送出する（呼び出し側でハンドリング）。
        """
        # 明示の base_dir 優先
        if base_dir is not None:
            base = base_dir.resolve() if isinstance(base_dir, Path) else Path(str(base_dir)).resolve()
        else:
            if project_id is None:
                raise ValueError("base_dir_or_project_id_required")
            try:
                # Services 層から Models を直接参照（簡易式クリーンアーキテクチャ方針）
                from models.projects import Projects
                from extensions import db
                proj = db.session.get(Projects, int(project_id))
            except Exception:
                proj = None
            if not proj or not getattr(proj, 'doc_path', None):
                raise ValueError("doc_path_not_set")
            base = Path(proj.doc_path).expanduser().resolve()
        if (not base.exists()) or (not base.is_dir()):
            raise ValueError("invalid_doc_path")
        self.base_dir = base

    # ---------------------- 既存: バックアップ比較 ----------------------
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

    # ---------------------- 追加: Git ベースの差分 ----------------------
    def _run_git(self, *args: str, timeout: int = 10) -> Tuple[int, str, str]:
        """git コマンドを実行し、(returncode, stdout, stderr) を返す。"""
        try:
            cp = subprocess.run(
                ["git", *args],
                cwd=str(self.base_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            out = cp.stdout.decode("utf-8", errors="ignore")
            err = cp.stderr.decode("utf-8", errors="ignore")
            return cp.returncode, out, err
        except FileNotFoundError:
            return 127, "", "git not found"
        except subprocess.TimeoutExpired:
            return 124, "", "git timeout"

    def _ensure_git_repo(self) -> None:
        # .git ディレクトリの存在または git rev-parse で確認
        if (self.base_dir / ".git").exists():
            return
        rc, out, err = self._run_git("rev-parse", "--is-inside-work-tree")
        if rc != 0 or (out.strip().lower() != "true"):
            raise ValueError("not_a_git_repo")

    def latest_git_diffs(
        self,
        *,
        staged: bool = False,
        include_untracked: bool = True,
        max_files: int = 200,
        max_patch_bytes: int = 500_000,
    ) -> List[DiffFile]:
        """
        Git の差分を取得して返す。既定では作業ツリーの未ステージ差分、staged=True でステージ済み差分。
        未追跡ファイルは include_untracked=True のときに /dev/null との比較として擬似パッチを生成する。
        """
        self._ensure_git_repo()

        # 変更ファイル一覧（ステータス付き）
        diff_args = ["diff", "--name-status", "-z"]
        if staged:
            diff_args.insert(1, "--staged")
        rc, out, err = self._run_git(*diff_args)
        if rc not in (0, 1):  # git diff は差分ありで 1 を返すことがある
            raise ValueError(f"git_diff_failed: {err.strip() or rc}")

        entries = [s for s in out.split("\x00") if s]
        changed: List[Tuple[str, str, Optional[str]]] = []  # (status, path, optional new_path)
        i = 0
        while i < len(entries):
            rec = entries[i]
            if "\t" in rec:
                # パターンA: 1トークンに「STATUS\tPATH(\tNEW)」が含まれているケース
                status, rest = rec.split("\t", 1)
                if status.startswith("R"):  # rename/copy（R### / C###）
                    if "\t" in rest:
                        old_path, new_path = rest.split("\t", 1)
                        changed.append(("R", new_path, old_path))
                        i += 1
                    else:
                        old_path = rest
                        new_path = entries[i + 1] if (i + 1) < len(entries) else None
                        if new_path:
                            changed.append(("R", new_path, old_path))
                        i += 2
                else:
                    path = rest
                    changed.append((status[:1], path, None))
                    i += 1
            else:
                # パターンB: ステータスとパスが別トークンで出力されるケース（Windows Git 等）
                status = rec
                if status.startswith("R"):  # rename/copy
                    old_path = entries[i + 1] if (i + 1) < len(entries) else None
                    new_path = entries[i + 2] if (i + 2) < len(entries) else None
                    if old_path and new_path:
                        changed.append(("R", new_path, old_path))
                    i += 3
                else:
                    path = entries[i + 1] if (i + 1) < len(entries) else None
                    if path:
                        changed.append((status[:1], path, None))
                    i += 2

        # 未追跡
        untracked: List[str] = []
        if include_untracked and not staged:
            rc_u, out_u, _ = self._run_git("ls-files", "--others", "--exclude-standard", "-z")
            if rc_u == 0:
                untracked = [s for s in out_u.split("\x00") if s]

        # 集約して重複排除（rename の old/new は new 優先）
        files_to_collect: List[Tuple[str, str, Optional[str]]] = []
        seen = set()
        for st, p, old in changed:
            key = (p, st)
            if key in seen:
                continue
            seen.add(key)
            files_to_collect.append((st, p, old))
        for p in untracked:
            if (p, "??") in seen:
                continue
            seen.add((p, "??"))
            files_to_collect.append(("??", p, None))

        results: List[DiffFile] = []
        for st, p, old in files_to_collect[:max_files]:
            status_norm = {
                "M": "modified",
                "A": "added",
                "D": "deleted",
                "R": "renamed",
                "??": "untracked",
            }.get(st, "modified")

            patch_text = ""
            truncated = False
            size = 0

            if st == "??":
                # /dev/null との比較で擬似パッチを生成（git diff --no-index）
                rc_p, out_p, _ = self._run_git("diff", "--no-index", "--patch", "--", "/dev/null", p)
                if rc_p in (0, 1):
                    patch_text = out_p
                else:
                    patch_text = f"--- /dev/null\n+++ b/{p}\n+<unavailable>"
                fpath = (self.base_dir / p)
                try:
                    size = fpath.stat().st_size
                except Exception:
                    size = 0
            else:
                # 個別パッチ取得（--staged は必要に応じて）
                args = ["diff"]
                if staged:
                    args.append("--staged")
                args += ["--patch", "--", p]
                rc_p, out_p, err_p = self._run_git(*args)
                if rc_p in (0, 1):
                    patch_text = out_p
                else:
                    patch_text = f"--- a/{p}\n+++ b/{p}\n-<unavailable>\n+<unavailable>\n"
                try:
                    size = (self.base_dir / p).stat().st_size
                except Exception:
                    size = 0

            if len(patch_text.encode("utf-8")) > max_patch_bytes:
                # 大きすぎるパッチは先頭だけ残す
                enc = patch_text.encode("utf-8")[:max_patch_bytes]
                patch_text = enc.decode("utf-8", errors="ignore") + "\n...<truncated>..."
                truncated = True

            results.append(DiffFile(
                path=p,
                status=status_norm,
                patch=patch_text,
                size=size,
                truncated=truncated,
            ))

        return results
