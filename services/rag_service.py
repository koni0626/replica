import os
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from services.project_service import ProjectService

try:
    from openai import OpenAI  # openai>=1.x
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore


class RagService:
    """
    プロジェクト(doc_path配下)のテキスト/コードをシンプルにRAG化する最小実装。
    - 文章は行ベースでチャンク化（max_chars/overlap）
    - OpenAI Embeddings(text-embedding-3-small 既定)でベクトル化
    - JSON Lines(index.jsonl) に {file, start, end, text, embedding} として保存
    - 検索はクエリを埋め込み→コサイン類似度で top_k を返す
    """

    def __init__(self, storage_root: Optional[Path] = None):
        self.storage_root = storage_root or Path("instance").resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.embedding_model = os.environ.get("RAG_EMBEDDING_MODEL", "text-embedding-3-small")

    # -----------------
    # 公開API
    # -----------------
    def build_index(
        self,
        project_id: int,
        include_exts: Optional[List[str]] = None,
        max_chars: int = 1500,
        overlap: int = 200,
        size_limit_bytes: Optional[int] = None,
    ) -> Dict:
        base_dir = self._resolve_doc_path(project_id)
        include_exts = self._normalize_exts(include_exts)
        out_dir = self._project_store_dir(project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        index_path = out_dir / "index.jsonl"

        files = self._collect_files(base_dir, include_exts)
        total_chunks = 0
        written = 0
        with open(index_path, "w", encoding="utf-8") as w:
            for f in files:
                chunks = self._chunk_file(f, max_chars=max_chars, overlap=overlap, size_limit=size_limit_bytes)
                if not chunks:
                    continue
                texts = [c[2] for c in chunks]
                embs = self._embed_texts(texts)
                for (start, end, text), emb in zip(chunks, embs):
                    rec = {
                        "file": str(f.relative_to(base_dir).as_posix()),
                        "start": start,
                        "end": end,
                        "text": text,
                        "embedding": emb,
                    }
                    w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1
                total_chunks += len(chunks)
        return {
            "ok": True,
            "project_id": project_id,
            "files": len(files),
            "chunks": total_chunks,
            "written": written,
            "index_path": str(index_path),
        }

    def update_index(
        self,
        project_id: int,
        paths: List[str],
        include_exts: Optional[List[str]] = None,
        max_chars: int = 1500,
        overlap: int = 200,
        size_limit_bytes: Optional[int] = None,
    ) -> Dict:
        """指定パスのみを部分的にインデックス更新する最小実装。
        - paths: doc_path からの相対パス（ファイル or ディレクトリ）。"/docs" や先頭スラッシュ、バックスラッシュは安全に正規化します。
        - include_exts: ディレクトリが指定された場合の対象拡張子フィルタ（未指定時は既定セット）。
        - 既存 index.jsonl から対象ファイルのレコードを除去し、新しいチャンクを追記します（atomic 置換）。
        """
        base_dir = self._resolve_doc_path(project_id)
        out_dir = self._project_store_dir(project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        index_path = out_dir / "index.jsonl"
        tmp_path = out_dir / "index.jsonl.tmp"

        include_exts = self._normalize_exts(include_exts)

        # 1) 対象ファイルの解決（doc_path 配下に制限）
        target_files: List[Path] = []
        target_rel_set: set[str] = set()

        def safe_to_rel(p: Path) -> Optional[str]:
            try:
                rel = p.resolve().relative_to(base_dir).as_posix()
                return rel
            except Exception:
                return None

        def add_file_if_ok(p: Path):
            if p.exists() and p.is_file():
                if not include_exts or p.suffix.lower() in set(include_exts):
                    rel = safe_to_rel(p)
                    if rel:
                        target_files.append(p)
                        target_rel_set.add(rel)

        for raw in paths or []:
            s = (raw or "").strip().replace("\\", "/")
            if not s:
                continue
            # 先頭スラッシュや docs/ は除去
            if s.startswith("/"):
                s = s[1:]
            if s.lower().startswith("docs/"):
                s = s[5:]
            # 危険な表現を拒否
            if ":" in s or ".." in s:
                continue
            candidate = (base_dir / s).resolve()
            try:
                candidate.relative_to(base_dir)
            except Exception:
                continue
            if candidate.is_dir():
                # ディレクトリ配下を include_exts で収集
                for ext in include_exts:
                    for p in candidate.rglob(f"*{ext}"):
                        add_file_if_ok(p)
            else:
                add_file_if_ok(candidate)

        # 重複排除
        uniq_files: List[Path] = []
        seen = set()
        for p in target_files:
            rp = str(p.resolve())
            if rp not in seen:
                seen.add(rp)
                uniq_files.append(p)
        target_files = uniq_files

        # 2) 既存 index.jsonl を読み、対象ファイルの行を除外して一時ファイルへ書き出し
        kept = 0
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as r, open(tmp_path, "w", encoding="utf-8") as w:
                for line in r:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        f = rec.get("file")
                        if f in target_rel_set:
                            # 対象ファイルは除外（上書きのため）
                            continue
                        w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        kept += 1
                    except Exception:
                        # 壊れた行はスキップ
                        continue
        else:
            # 無ければ新規作成
            with open(tmp_path, "w", encoding="utf-8"):
                pass

        # 3) 対象ファイルをチャンク化→埋め込み→追記
        total_chunks = 0
        written = 0
        if target_files:
            with open(tmp_path, "a", encoding="utf-8") as w:
                for f in target_files:
                    chunks = self._chunk_file(f, max_chars=max_chars, overlap=overlap, size_limit=size_limit_bytes)
                    if not chunks:
                        continue
                    texts = [c[2] for c in chunks]
                    embs = self._embed_texts(texts)
                    rel = f.relative_to(base_dir).as_posix()
                    for (start, end, text), emb in zip(chunks, embs):
                        rec = {
                            "file": rel,
                            "start": start,
                            "end": end,
                            "text": text,
                            "embedding": emb,
                        }
                        w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        written += 1
                    total_chunks += len(chunks)

        # 4) 原子置換
        os.replace(tmp_path, index_path)

        return {
            "ok": True,
            "project_id": project_id,
            "targets": len(target_files),
            "kept": kept,
            "chunks": total_chunks,
            "written": written,
            "index_path": str(index_path),
        }
    def index_plain_text(
        self,
        project_id: int,
        rel_name: str,
        text: str,
        max_chars: int = 1500,
        overlap: int = 200,
        save_original: bool = True,
    ) -> Dict:
        """任意の生テキスト（プロンプト等）をRAGインデックスへ登録する。
        - instance/<project_id>/index.jsonl に {file, start, end, text, embedding} を追記
        - 必要に応じて原文も instance/<project_id>/prompts/<rel_name> に保存
        - file フィールドには "prompts/<rel_name>" を格納し、出典を区別する
        """
        out_dir = self._project_store_dir(project_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        index_path = out_dir / "index.jsonl"
        tmp_path = out_dir / "index.jsonl.tmp"

        # rel_name の正規化と安全化
        name = (rel_name or "prompt.txt").strip().replace("\\", "/")
        if name.startswith("/"):
            name = name[1:]
        if name.lower().startswith("docs/"):
            name = name[5:]
        # 危険な表現は無害化
        if ":" in name:
            name = name.replace(":", "_")
        if ".." in name:
            name = name.replace("..", "")
        logical_file = f"prompts/{name}"

        # 原文の保存（任意）
        if save_original:
            prompts_dir = out_dir / "prompts"
            target_path = prompts_dir / name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(target_path, "w", encoding="utf-8") as fw:
                    fw.write(text or "")
            except Exception:
                # 原文保存に失敗してもインデックスは続行
                pass

        # 既存 index.jsonl から同一 logical_file の行を除外しつつ退避
        kept = 0
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as r, open(tmp_path, "w", encoding="utf-8") as w:
                for line in r:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                        f = rec.get("file")
                        if f == logical_file:
                            # 同一ファイルは置換のため除外
                            continue
                        w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        kept += 1
                    except Exception:
                        continue
        else:
            with open(tmp_path, "w", encoding="utf-8"):
                pass

        # テキストをチャンク化→埋め込み→追記
        chunks: List[Tuple[int, int, str]] = self._chunk_string(text or "", max_chars=max_chars, overlap=overlap)
        total_chunks = len(chunks)
        written = 0
        if chunks:
            texts = [c[2] for c in chunks]
            embs = self._embed_texts(texts)
            with open(tmp_path, "a", encoding="utf-8") as w:
                for (start, end, t), emb in zip(chunks, embs):
                    rec = {
                        "file": logical_file,
                        "start": start,
                        "end": end,
                        "text": t,
                        "embedding": emb,
                    }
                    w.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    written += 1

        os.replace(tmp_path, index_path)
        return {
            "ok": True,
            "project_id": project_id,
            "file": logical_file,
            "kept": kept,
            "chunks": total_chunks,
            "written": written,
            "index_path": str(index_path),
        }

    def _chunk_string(self, s: str, *, max_chars: int, overlap: int) -> List[Tuple[int, int, str]]:
        """文字列を行ベースにチャンク化する（_chunk_file と同等の挙動）。"""
        try:
            chunks: List[Tuple[int, int, str]] = []
            if not s:
                return chunks
            lines = s.splitlines()
            buf: List[str] = []
            buf_chars = 0
            cur_start = 1
            line_no = 0
            back_lines_from_chars = lambda ch: max(0, (ch if ch > 0 else 0) // 80)
            for raw in lines:
                line_no += 1
                l = raw
                if buf_chars + len(l) + 1 <= max_chars:
                    buf.append(l)
                    buf_chars += len(l) + 1
                else:
                    end = line_no - 1
                    text_block = "\n".join(buf) + "\n" if buf else ""
                    if text_block.strip():
                        chunks.append((cur_start, end, text_block))
                    back = back_lines_from_chars(overlap)
                    cur_start = max(1, end - back + 1)
                    keep_from = max(0, len(buf) - back)
                    buf = buf[keep_from:]
                    buf_chars = sum(len(x) + 1 for x in buf)
                    buf.append(l)
                    buf_chars += len(l) + 1
            # 末尾
            text_block = "\n".join(buf) + "\n" if buf else ""
            if text_block.strip():
                chunks.append((cur_start, line_no, text_block))
            return chunks
        except Exception:
            return []
    def query_text(self, project_id: int, query: str, top_k: int = 8) -> List[Dict]:
        base_dir = self._resolve_doc_path(project_id)
        out_dir = self._project_store_dir(project_id)
        index_path = out_dir / "index.jsonl"
        if not index_path.exists():
            return []
        q_emb = self._embed_texts([query])[0]

        scored: List[Tuple[float, Dict]] = []
        with open(index_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    emb = rec.get("embedding") or []
                    score = self._cosine(q_emb, emb)
                    scored.append((score, rec))
                except Exception:
                    continue
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = []
        for s, rec in scored[: max(1, top_k)]:
            hits.append({
                "file": rec.get("file"),
                "start": rec.get("start"),
                "end": rec.get("end"),
                "text": rec.get("text"),
                "score": float(s),
            })
        return hits

    # -----------------
    # 内部ユーティリティ
    # -----------------
    def _resolve_doc_path(self, project_id: int) -> Path:
        ps = ProjectService()
        proj = ps.fetch_by_id(project_id)
        if not proj or not getattr(proj, "doc_path", None):
            raise ValueError("doc_path_not_set")
        base = Path(proj.doc_path).expanduser().resolve()
        if (not base.exists()) or (not base.is_dir()):
            raise ValueError("invalid_doc_path")
        return base

    def _project_store_dir(self, project_id: int) -> Path:
        return self.storage_root / str(project_id)

    def _normalize_exts(self, exts: Optional[List[str]]) -> List[str]:
        if not exts:
            return [
                ".py", ".js", ".ts", ".tsx", ".php", ".html", ".css", ".scss",
                ".md", ".txt", ".json", ".yml", ".yaml"
            ]
        norm: List[str] = []
        for x in exts:
            s = (x or "").strip().lower()
            if not s:
                continue
            if not s.startswith("."):
                s = "." + s
            norm.append(s)
        return norm

    def _collect_files(self, base_dir: Path, include_exts: List[str]) -> List[Path]:
        files: List[Path] = []
        for ext in include_exts:
            for p in base_dir.rglob(f"*{ext}"):
                if p.is_file():
                    files.append(p)
        # 一意性を担保
        uniq = []
        seen = set()
        for p in files:
            rp = str(p.resolve())
            if rp not in seen:
                seen.add(rp)
                uniq.append(p)
        return uniq

    def _chunk_file(self, path: Path, *, max_chars: int, overlap: int, size_limit: Optional[int]) -> List[Tuple[int, int, str]]:
        """大きなファイルも扱えるよう、ストリーム的に分割する。
        - size_limit: None のときはサイズ上限なし。それ以外は上限超過でスキップ。
        - max_chars/overlap は文字数ベース。overlap は近似的に行へ換算。
        """
        try:
            if size_limit is not None and path.stat().st_size > size_limit:
                # 上限を超える巨大ファイルはスキップ（将来: 分割読みへの拡張余地）
                # ただし要件「大きなファイルもRAG化」に合わせるため、
                # size_limit=None を既定にし、既定ではスキップしない方針に変更。
                return []
            # 逐次読み込みで巨大ファイルでもメモリ圧迫を避ける
            chunks: List[Tuple[int, int, str]] = []
            buf = []
            buf_chars = 0
            cur_start = 1
            line_no = 0
            back_lines_from_chars = lambda ch: max(0, (ch if ch > 0 else 0) // 80)
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line_no += 1
                    l = line.rstrip("\n")
                    if buf_chars + len(l) + 1 <= max_chars:
                        buf.append(l)
                        buf_chars += len(l) + 1
                    else:
                        end = line_no - 1
                        text_block = "\n".join(buf) + "\n" if buf else ""
                        if text_block.strip():
                            chunks.append((cur_start, end, text_block))
                        # overlap のために直近のテキストから行数を逆算
                        back = back_lines_from_chars(overlap)
                        cur_start = max(1, end - back + 1)
                        # バッファを巻き戻した分で再構築
                        # cur_start..end の範囲を一部保持
                        keep_from = max(0, len(buf) - back)
                        buf = buf[keep_from:]
                        buf_chars = sum(len(x) + 1 for x in buf)
                        # 今回の行を追加
                        buf.append(l)
                        buf_chars += len(l) + 1
                # ファイル末尾
                text_block = "\n".join(buf) + "\n" if buf else ""
                if text_block.strip():
                    chunks.append((cur_start, line_no, text_block))
            return chunks
        except Exception:
            return []

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if OpenAI is None:
            raise RuntimeError("openai library is not available")
        client = OpenAI()
        # OpenAIのバッチ制限に合わせて分割（安全側: 100件単位）
        out: List[List[float]] = []
        batch = 100
        for i in range(0, len(texts), batch):
            part = texts[i:i + batch]
            resp = client.embeddings.create(model=self.embedding_model, input=part)
            for d in resp.data:
                out.append(list(d.embedding))
        return out

    def _cosine(self, a: List[float], b: List[float]) -> float:
        if not a or not b:
            return 0.0
        # 安全: 次元が異なる場合は短い方に合わせる
        n = min(len(a), len(b))
        s = 0.0
        sa = 0.0
        sb = 0.0
        for i in range(n):
            va = float(a[i])
            vb = float(b[i])
            s += va * vb
            sa += va * va
            sb += vb * vb
        if sa == 0.0 or sb == 0.0:
            return 0.0
        import math
        return s / (math.sqrt(sa) * math.sqrt(sb))
