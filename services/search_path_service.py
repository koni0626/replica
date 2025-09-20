from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Any

from services.project_service import ProjectService


class SearchPathService:
    """
    検索パス（include/exclude）の永続化と取得、ツリー生成を担当。
    保存場所: instance/<project_id>/search_paths.json
    フォーマット:
    {
      "version": 1,
      "includes": ["controllers", "services"],
      "excludes": ["controllers/legacy"]
    }
    """

    VERSION = 1

    def _instance_dir(self, project_id: int) -> Path:
        base = Path.cwd() / "instance" / str(project_id)
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _state_path(self, project_id: int) -> Path:
        return self._instance_dir(project_id) / "search_paths.json"

    def load_state(self, project_id: int) -> Dict[str, Any]:
        p = self._state_path(project_id)
        REQUIRED_EXCLUDES = {".git", "vendor"}

        def _norm(s: str) -> str:
            return str(s).strip().replace("\\", "/").strip("/")

        if not p.exists():
            return {"version": self.VERSION, "includes": [], "excludes": sorted(list(REQUIRED_EXCLUDES))}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            inc = data.get("includes") or []
            exc = data.get("excludes") or []
            if not isinstance(inc, list) or not isinstance(exc, list):
                return {"version": self.VERSION, "includes": [], "excludes": sorted(list(REQUIRED_EXCLUDES))}
            # 正規化
            inc_norm = [_norm(x) for x in inc if _norm(x)]
            exc_norm = [_norm(x) for x in exc if _norm(x)]
            # includes から .git / vendor を除外
            inc_norm = [x for x in inc_norm if
                        x not in REQUIRED_EXCLUDES and not any(x.startswith(f"{rx}/") for rx in REQUIRED_EXCLUDES)]
            # excludes へ強制追加
            exc_norm.extend(REQUIRED_EXCLUDES)
            return {
                "version": int(data.get("version") or self.VERSION),
                "includes": sorted(list(dict.fromkeys(inc_norm))),
                "excludes": sorted(list(dict.fromkeys(exc_norm))),
            }
        except Exception:
            # 壊れている場合は初期状態（必須除外のみ）
            return {"version": self.VERSION, "includes": [], "excludes": sorted(list(REQUIRED_EXCLUDES))}

    def save_state(self, project_id: int, includes: List[str], excludes: List[str]) -> Dict[str, Any]:
        """
        ツリー設定を保存する。
        - includes/excludes を正規化（パス区切りを / に統一、前後の / を除去）
        - .git / vendor / .idea は常に excludes に含める（強制）
        - includes に .git / vendor / .idea が紛れていた場合は除去する
        """
        REQUIRED_EXCLUDES = {".git", "vendor", ".idea"}

        def _norm(s: str) -> str:
            return str(s).strip().replace("\\", "/").strip("/")

        # 正規化
        inc_raw = [x for x in (includes or []) if str(x).strip()]
        exc_raw = [x for x in (excludes or []) if str(x).strip()]
        inc_norm = [_norm(x) for x in inc_raw if _norm(x)]
        exc_norm = [_norm(x) for x in exc_raw if _norm(x)]
        # includes から .git / vendor / .idea を除外（トップレベル表記のみ強制排除）
        inc_norm = [x for x in inc_norm if
                    x not in REQUIRED_EXCLUDES and not any(x.startswith(f"{rx}/") for rx in REQUIRED_EXCLUDES)]
        # excludes に .git / vendor / .idea を必ず追加
        exc_norm.extend(REQUIRED_EXCLUDES)
        # 重複排除（順序を保ったユニーク化→最終的に既存仕様どおり sorted）
        inc_final = sorted(list(dict.fromkeys(inc_norm)))
        exc_final = sorted(list(dict.fromkeys(exc_norm)))
        state = {
            "version": self.VERSION,
            "includes": inc_final,
            "excludes": exc_final,
        }
        p = self._state_path(project_id)
        p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        return state

    def _doc_base(self, project_id: int) -> Path:
        ps = ProjectService()
        proj = ps.fetch_by_id(project_id)
        if not proj or not getattr(proj, "doc_path", None):
            raise ValueError("doc_path_not_set")
        base = Path(proj.doc_path).expanduser().resolve()
        if not base.exists() or not base.is_dir():
            raise ValueError("invalid_doc_path")
        return base

    def build_tree(self, project_id: int, rel: str = "") -> List[Dict[str, Any]]:
        """
        doc_path 配下の "直下1階層のみ" のディレクトリ一覧を返す（Lazy Load 用）。
        - 巨大/不要ディレクトリは除外（.git / vendor / .github / logs / node_modules / .venv / __pycache__ / .idea）
        - rel を指定すると、そのサブディレクトリ直下のみを返す
        返却ノード: { name, rel, has_children }
        """
        base = self._doc_base(project_id)

        EXCLUDED_NAMES = {".git", "vendor", ".github", "logs", ".venv", "__pycache__", ".idea"}

        def is_excluded_path(p: Path) -> bool:
            try:
                parts = p.resolve().relative_to(base).parts
            except Exception:
                # base 外は対象外
                return True
            return any(part in EXCLUDED_NAMES for part in parts)

        # 開始ディレクトリを決定
        start = base
        rel_norm = (rel or "").strip().replace("\\", "/").strip("/")
        if rel_norm:
            candidate = (base / rel_norm).resolve()
            try:
                candidate.relative_to(base)
            except Exception:
                candidate = base
            start = base if is_excluded_path(candidate) else candidate

        if not start.exists() or not start.is_dir():
            start = base

        nodes: List[Dict[str, Any]] = []
        try:
            with os.scandir(start) as it:
                for entry in it:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    name = entry.name
                    if name in EXCLUDED_NAMES:
                        continue
                    abs_dir = Path(entry.path).resolve()
                    if is_excluded_path(abs_dir):
                        continue

                    # has_children を軽量に判定（除外対象を考慮）
                    has_children = False
                    try:
                        with os.scandir(abs_dir) as it2:
                            for ch in it2:
                                if ch.is_dir(follow_symlinks=False) and ch.name not in EXCLUDED_NAMES:
                                    # 子配下の除外も考慮（絶対パスで再チェック）
                                    ch_abs = Path(ch.path).resolve()
                                    if not is_excluded_path(ch_abs):
                                        has_children = True
                                        break
                    except Exception:
                        has_children = False

                    # rel の計算（base 相対の POSIX 表現）
                    if start == base:
                        rel_child = name
                    else:
                        # f-string 内でのバックスラッシュ表現を避けるため、f 文字列を使わずに生成
                        rel_child = str(abs_dir.relative_to(base)).replace("\\", "/")

                    nodes.append({
                        "name": name,
                        "rel": rel_child,
                        "has_children": has_children,
                    })
        except Exception:
            # アクセス不可などは空配列
            return []

        # 名前順で安定化
        nodes.sort(key=lambda x: x["name"].lower())
        return nodes


    @staticmethod
    def to_globs_from_state(state: Dict[str, Any]) -> Dict[str, List[str]]:
        """
        保存状態から search_grep 向けの include_globs / exclude_globs を生成。
        - includes は path/** へ
        - excludes は path/** へ（exclude_globs として使用推奨）
        空なら空配列
        """
        inc = []
        exc = []
        for s in state.get("includes", []) or []:
            s = str(s).strip().replace("\\", "/").strip("/")
            if s:
                inc.append(f"{s}/**")
        for s in state.get("excludes", []) or []:
            s = str(s).strip().replace("\\", "/").strip("/")
            if s:
                exc.append(f"{s}/**")
        return {"include_globs": inc, "exclude_globs": exc}
