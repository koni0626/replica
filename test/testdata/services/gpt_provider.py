from dataclasses import dataclass
from typing import Optional, Iterator, List, Dict, Any, Tuple, Union
import os
import base64
import zipfile
from pathlib import Path
from datetime import datetime
import re
import tempfile
from pathlib import Path
import difflib

# LangChain
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool

from services.doc_service import DocService
from models.knowledge import Knowledge

class RepoSandbox:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"doc_path not found or not a directory: {self.root}")

    def _safe(self, rel: str) -> Path:
        p = (self.root / rel.lstrip("/\\")).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("Path escapes repo root")
        return p

    def list_files(self, patterns: list[str] | None = None, max_files: int = 2000) -> str:
        # ざっくりツリー表示（大きすぎる/バイナリは除外）
        exts_skip = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".jar"}
        files = []
        for f in self.root.rglob("*"):
            if f.is_file() and f.suffix.lower() not in exts_skip and f.stat().st_size < 2_000_000:
                files.append(str(f.relative_to(self.root)))
                if len(files) >= max_files: break
        if patterns:
            import fnmatch
            files = [x for x in files if any(fnmatch.fnmatch(x, pat) for pat in patterns)]
        return "ROOT: " + str(self.root) + "\n" + "\n".join(files or ["(empty)"])

    def read_text(self, relpath: str, max_bytes: int = 200_000) -> str:
        p = self._safe(relpath)
        if not p.is_file():
            return f"error=not found: {relpath}"
        data = p.read_bytes()
        if len(data) > max_bytes:
            return data[:max_bytes].decode("utf-8", errors="ignore") + "\n...<truncated>..."
        return data.decode("utf-8", errors="ignore")

    def write_text(self, relpath: str, content: str) -> str:
        p = self._safe(relpath)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content or "", encoding="utf-8")
        return f"wrote={relpath}"

    def apply_unified_diff(self, diff_text: str) -> str:
        # 単純な unified diff 適用（失敗時は error を返す）
        # 1ファイルずつ抽出して適用
        patched = []
        current_file = None
        orig = None
        new = None
        blocks = {}
        lines = diff_text.splitlines(keepends=False)
        for i, line in enumerate(lines):
            if line.startswith("--- "):
                # ファイル開始
                # --- a/path
                # +++ b/path
                try:
                    a_path = line.split(" ", 1)[1].strip()
                    b_path = lines[i+1].split(" ", 1)[1].strip()  # +++ 行
                except Exception:
                    return "error=invalid diff header"
                # diff によって a/ or b/ が付く想定。右辺のパスを採用
                current_file = b_path.replace("b/", "").replace("a/", "")
                orig = []
                new = []
                # 以降の @@ チャンクを収集
                j = i + 2
                hunk = []
                while j < len(lines) and not lines[j].startswith("--- "):
                    hunk.append(lines[j]); j += 1
                blocks[current_file] = "\n".join(hunk)
        if not blocks:
            return "error=no file blocks"

        for rel, body in blocks.items():
            # 非厳密：現行ファイルを読み取り → difflib でパッチ相当を適用
            old_text = self.read_text(rel)
            if old_text.startswith("error="):
                return old_text
            old_lines = old_text.splitlines(keepends=True)
            # ここでは AI に new 側全文を作らせる戦略に変える（簡易適用）
            # body の最後に "+++NEW\n<file content>" のような合図を採用してもよい
            # ただしまずは安全に失敗させる
            return "error=apply_unified_diff is intentionally strict; prefer repo_write with full new content"

        return f"patched={len(patched)}"


@dataclass
class GptResult:
    content: str
    provider: Optional[str] = None
    model_name: Optional[str] = None


# -----------------------------
# 生成物ストア（クロスプラットフォーム対応）
# -----------------------------
class ArtifactStore:
    """
    - 書き込み可能なベースディレクトリを自動選択（Windows/macOS/Linux対応）
    - ルート脱出の防止
    - テキスト/バイナリ書き込みとZIP化
    """
    def __init__(self, base_dir: Optional[Union[str, Path]] = None):
        self.base_dir = self._resolve_base_dir(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.root = self._make_new_root("project")
        self.manifest: List[Dict[str, Any]] = []

    def _resolve_base_dir(self, base_dir: Optional[Union[str, Path]]) -> Path:
        # 1) 明示指定 or 環境変数
        if base_dir:
            cand = Path(str(base_dir)).expanduser().resolve()
            cand.mkdir(parents=True, exist_ok=True)
            self._ensure_writable(cand)
            return cand

        env = os.getenv("APP_GENERATED_DIR") or os.getenv("GENERATED_BASE_DIR")
        if env:
            cand = Path(env).expanduser().resolve()
            cand.mkdir(parents=True, exist_ok=True)
            self._ensure_writable(cand)
            return cand

        # 2) OSごとの候補（上から順に試す）
        candidates = []

        # Linux/コンテナ向けの慣例
        candidates.append(Path("/mnt/data/generated"))

        # ユーザーHOME配下（WindowsでもmacOSでも可）
        candidates.append(Path.home() / "GeneratedArtifacts")

        # プロジェクトカレント配下
        candidates.append(Path.cwd() / "generated")

        # 一時ディレクトリ配下
        candidates.append(Path(tempfile.gettempdir()) / "generated")

        for cand in candidates:
            try:
                cand.mkdir(parents=True, exist_ok=True)
                self._ensure_writable(cand)
                return cand.resolve()
            except Exception:
                continue

        # どこにも作れなかった場合は明示的にエラー
        raise RuntimeError("No writable base directory found for ArtifactStore")

    def _ensure_writable(self, directory: Path) -> None:
        test = directory / ".write_test.tmp"
        with open(test, "w", encoding="utf-8") as f:
            f.write("ok")
        test.unlink(missing_ok=True)

    def _slug(self, s: str) -> str:
        s = s.strip().lower()
        s = re.sub(r"[^a-z0-9_\-]+", "-", s)
        s = re.sub(r"-{2,}", "-", s).strip("-")
        return s or "project"

    def _make_new_root(self, name: str) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = self._slug(name)
        root = self.base_dir / f"{slug}_{ts}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def set_project_root(self, name: str) -> str:
        self.root = self._make_new_root(name)
        self.manifest.clear()
        return str(self.root)

    def _safe_join(self, rel: str) -> Path:
        rel = rel.lstrip("/\\")
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("Path escapes project root")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def write_text(self, path: str, content: str) -> str:
        p = self._safe_join(path)
        p.write_text(content or "", encoding="utf-8")
        self.manifest.append({"path": str(p.relative_to(self.root)), "bytes": p.stat().st_size, "type": "text"})
        return str(p.relative_to(self.root))

    def write_binary_b64(self, path: str, b64_content: str) -> str:
        p = self._safe_join(path)
        data = base64.b64decode(b64_content or "")
        with open(p, "wb") as f:
            f.write(data)
        self.manifest.append({"path": str(p.relative_to(self.root)), "bytes": p.stat().st_size, "type": "binary"})
        return str(p.relative_to(self.root))

    def list_files(self) -> List[str]:
        return [str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()]

    def tree_text(self) -> str:
        lines = []
        root_str = str(self.root)
        for p in sorted(self.root.rglob("*")):
            rel = str(p.relative_to(self.root))
            if p.is_dir():
                lines.append(f"[D] {rel}")
            else:
                size = p.stat().st_size
                lines.append(f"[F] {rel}  ({size} bytes)")
        if not lines:
            lines.append("(empty)")
        return f"ROOT: {root_str}\n" + "\n".join(lines)

    def zip(self, zip_name: Optional[str] = None) -> str:
        if not zip_name:
            zip_name = f"{self.root.name}.zip"
        zip_path = self.root.parent / zip_name
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for f in self.root.rglob("*"):
                if f.is_file():
                    zf.write(f, arcname=str(f.relative_to(self.root)))
        return str(zip_path)


class GptProvider:
    def __init__(
        self,
        model: str = "gpt-5",
        temperature: float = 0.3,
        timeout: int = 60,
        max_retries: int = 2,
    ):
        self.llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "あなたは有能なソフトウェア設計アシスタントです。"
                    "出力は必ず日本語で、Markdown整形（見出し・箇条書き・表など）で分かりやすく書いてください。"
                ),
                ("human", "{user_prompt}"),
            ]
        )
        self.parser = StrOutputParser()

    # --- 既存API ---
    def generate(self, prompt: str) -> GptResult:
        chain = self.prompt | self.llm | self.parser
        content = chain.invoke({"user_prompt": prompt})
        return GptResult(content=content, provider="langchain+openai", model_name=self.llm.model_name)

    def stream(self, prompt: str) -> Iterator[str]:
        chain = self.prompt | self.llm
        for chunk in chain.stream({"user_prompt": prompt}):
            piece = getattr(chunk, "content", "") or ""
            if piece:
                yield piece

    # --- メッセージ構築（ナレッジMarkdown注入） ---
    def _build_messages(
        self,
        project_id: int,
        new_prompt: str,
        svc: DocService,
        history_limit: int = 20,
        *,
        use_knowledge: bool = True,
        knowledge_limit: int = 8,
        knowledge_categories: Optional[List[str]] = None,
    ):
        messages: List = []
        messages.append(SystemMessage(content=(
            "あなたは有能なソフトウェア設計アシスタントです。"
            "出力は必ず日本語で、Markdown整形（見出し・箇条書き・表など）で分かりやすく書いてください。"
        )))
        if use_knowledge and Knowledge is not None:
            kn = self._fetch_knowledge(project_id=project_id, limit=knowledge_limit, categories=knowledge_categories)
            if kn:
                messages.append(SystemMessage(content=(
                    "以下はプロジェクトのナレッジベース（Markdown）です。"
                    "この内容を最優先で尊重して回答してください。\n\n" + kn
                )))
        history = svc.fetch_history(project_id=project_id, limit=history_limit, newest_first=False)
        for memo in history:
            if getattr(memo, "prompt", None):
                messages.append(HumanMessage(content=memo.prompt))
            if getattr(memo, "content", None):
                messages.append(AIMessage(content=memo.content))
        messages.append(HumanMessage(content=new_prompt))
        return messages

    def _fetch_knowledge(self, *, project_id: int, limit: int, categories: Optional[List[str]] = None) -> str:
        try:
            q = Knowledge.query.filter_by(project_id=project_id)
            if categories:
                q = q.filter(Knowledge.category.in_(categories))
            q = q.order_by(Knowledge.order.asc(), Knowledge.updated_at.desc(), Knowledge.knowledge_id.asc())
            rows = q.limit(limit).all()
        except Exception:
            rows = []
        if not rows:
            return ""
        parts: List[str] = []
        for r in rows:
            title = r.title or ""
            head = f"## {title}" if title else ""
            body = (r.content or "").strip()
            parts.append(f"{head}\n{body}" if head else body)
        return "\n\n".join(parts)

    def generate_with_history(
        self,
        *,
        project_id: int,
        prompt: str,
        svc: DocService,
        history_limit: int = 200,
        use_knowledge: bool = True,
        knowledge_limit: int = 8,
        knowledge_categories: Optional[List[str]] = None,
    ) -> GptResult:
        messages = self._build_messages(
            project_id, prompt, svc,
            history_limit=history_limit,
            use_knowledge=use_knowledge,
            knowledge_limit=knowledge_limit,
            knowledge_categories=knowledge_categories,
        )
        res = self.llm.invoke(messages)
        return GptResult(content=res.content, provider="langchain+openai", model_name=self.llm.model_name)

    def stream_with_history(
        self,
        *,
        project_id: int,
        prompt: str,
        svc: DocService,
        history_limit: int = 200,
        use_knowledge: bool = True,
        knowledge_limit: int = 8,
        knowledge_categories: Optional[List[str]] = None,
    ):
        messages = self._build_messages(
            project_id, prompt, svc,
            history_limit=history_limit,
            use_knowledge=use_knowledge,
            knowledge_limit=knowledge_limit,
            knowledge_categories=knowledge_categories,
        )
        for chunk in self.llm.stream(messages):
            piece = getattr(chunk, "content", "") or ""
            if piece:
                yield piece

    # -----------------------------
    # LangChainツールでコード生成
    # -----------------------------
    def generate_project_with_tools(
        self,
        *,
        project_id: int,
        spec_markdown: str,
        svc: DocService,
        history_limit: int = 50,
        use_knowledge: bool = True,
        knowledge_limit: int = 8,
        knowledge_categories: Optional[List[str]] = None,
        project_name: str = "generated_project",
        create_zip: bool = True,
        extra_system_rules: Optional[str] = None,
        max_tool_turns: int = 80,  # ← 少し余裕を持たせる
    ) -> GptResult:
        messages = self._build_messages(
            project_id=project_id,
            new_prompt=(
                "以下の仕様から、完全な動作に必要な全ファイルを作成してください。"
                "LangChainツール（set_project_root / write_file / write_binary_file / list_files / finalize_zip）"
                "のみでファイルを出力すること。本文にコードを貼らず、必ずツールで書き込みを行ってください。\n\n"
                f"仕様:\n{spec_markdown}"
            ),
            svc=svc,
            history_limit=history_limit,
            use_knowledge=use_knowledge,
            knowledge_limit=knowledge_limit,
            knowledge_categories=knowledge_categories,
        )

        system_rules = (
            "### ツール使用ルール\n"
            "- 最初に set_project_root(name) を1回呼ぶ。\n"
            "- すべてのファイルは write_file / write_binary_file で作成する。\n"
            "- 必要に応じて list_files() で構成確認。\n"
            "- 最後に finalize_zip(zip_name?) を呼び ZIP の絶対パスを得る。\n"
            "- 各ファイルは完全な内容を書く（省略禁止）。\n"
        )
        if extra_system_rules:
            system_rules += "\n" + extra_system_rules
        messages.insert(0, SystemMessage(content=system_rules))

        store = ArtifactStore()

        # 2) ストアとツール定義（←ここから置換）
        store = ArtifactStore()

        @tool("set_project_root")
        def _set_project_root(name: str) -> str:
            """Set (create) a fresh project root directory under a writable base directory.

            Args:
                name: Preferred project name. Will be slugified and timestamped.
            Returns:
                A string 'project_root=<abs_path>'.
            """
            path = store.set_project_root(name or "project")
            return f"project_root={path}"

        @tool("write_file")
        def _write_file(path: str, content: str) -> str:
            """Write a UTF-8 text file at the given relative path under the current project root.

            Args:
                path: Relative file path (e.g., 'app/__init__.py').
                content: File content in UTF-8 text.
            Returns:
                A string 'wrote=<relative_path>'.
            """
            rel = store.write_text(path, content)
            return f"wrote={rel}"

        @tool("write_binary_file")
        def _write_binary_file(path: str, base64_content: str) -> str:
            """Write a binary file from Base64 data at the given relative path under the current project root.

            Args:
                path: Relative file path.
                base64_content: File content encoded in Base64.
            Returns:
                A string 'wrote_binary=<relative_path>'.
            """
            rel = store.write_binary_b64(path, base64_content)
            return f"wrote_binary={rel}"

        @tool("list_files")
        def _list_files() -> str:
            """Return a textual tree view of files and directories under the current project root.

            Returns:
                A multi-line string including 'ROOT: <abs_path>' and [D]/[F] entries.
            """
            return store.tree_text()

        @tool("finalize_zip")
        def _finalize_zip(zip_name: Optional[str] = None) -> str:
            """Zip the generated artifacts and return an absolute path to the zip archive.

            Args:
                zip_name: Optional custom filename for the zip.
            Returns:
                A string 'zip_path=<abs_path_to_zip>'.
            """
            z = store.zip(zip_name=zip_name)
            return f"zip_path={z}"
        # （置換ここまで）


        tools = [_set_project_root, _write_file, _write_binary_file, _list_files, _finalize_zip]
        llm_with_tools = self.llm.bind_tools(tools)

        # --- 実行ループ（assistant→toolの順序を厳守） ---
        conversation: List = messages[:]
        for _ in range(max_tool_turns):
            ai_msg = llm_with_tools.invoke(conversation)
            tool_calls = getattr(ai_msg, "tool_calls", None)

            if not tool_calls:
                final_text = (ai_msg.content or "").strip()
                summary = f"{final_text}\n\n---\nArtifacts root: {store.root}\n{store.tree_text()}\n"
                return GptResult(content=summary, provider="langchain+openai", model_name=self.llm.model_name)

            conversation.append(ai_msg)  # assistant（tool_callsあり）

            latest_tool_messages: List[ToolMessage] = []
            for call in tool_calls:
                name = call.get("name")
                args = call.get("args", {}) or {}
                call_id = call.get("id")
                try:
                    if name == "set_project_root":
                        result = _set_project_root.invoke(args)
                    elif name == "write_file":
                        result = _write_file.invoke(args)
                    elif name == "write_binary_file":
                        result = _write_binary_file.invoke(args)
                    elif name == "list_files":
                        result = _list_files.invoke(args)
                    elif name == "finalize_zip":
                        result = _finalize_zip.invoke(args)
                    else:
                        result = f"error=Unknown tool: {name}"
                except Exception as e:
                    result = f"error={type(e).__name__}: {e}"
                latest_tool_messages.append(ToolMessage(content=str(result), tool_call_id=call_id))

            conversation.extend(latest_tool_messages)

            # finalize_zip で終わる
            if any(("zip_path=" in (tm.content or "")) for tm in latest_tool_messages):
                zip_line = [tm.content for tm in latest_tool_messages if "zip_path=" in (tm.content or "")]
                zip_info = zip_line[-1] if zip_line else ""
                summary = (
                    "ZIPを作成しました。生成物の情報です。\n\n"
                    f"- {zip_info}\n- project_root={store.root}\n\n"
                    "### ファイルツリー\n" + store.tree_text() + "\n"
                )
                return GptResult(content=summary, provider="langchain+openai", model_name=self.llm.model_name)

        # 最大回数超過
        fallback = (
            "（注意）最大ツール実行回数に達しました。おそらくファイル書き込みに失敗しています。\n"
            f"Artifacts base: {store.base_dir}\n"
            f"{store.tree_text()}\n"
            "環境変数 APP_GENERATED_DIR を書き込み可能なパスに設定して再実行してください。"
        )
        return GptResult(content=fallback, provider="langchain+openai", model_name=self.llm.model_name)

    def refactor_with_repo_tools(
            self,
            *,
            project_id: int,
            doc_path: str,
            task_prompt: str,
            svc: DocService,
            history_limit: int = 50,
            knowledge_limit: int = 8,
    ) -> GptResult:
        # 既存の履歴/ナレッジ注入を流用
        messages = self._build_messages(
            project_id=project_id,
            new_prompt=(
                "あなたはプロのリファクタ支援エンジニアです。"
                "以下のツールのみでコードを調査・修正してください。"
                "- repo_list(patterns?) で対象を把握\n"
                "- repo_read で該当ファイルを読み取り\n"
                "- 修正は原則 repo_write で**完全な新内容**を書き込む（部分貼り付け禁止）\n"
                "- どうしても diff が必要な場合のみ repo_patch（ただし適用は厳格で失敗しやすい）\n"
                "出力本文にコード全文を貼るのではなく、ツールを呼び出してください。\n\n"
                f"リクエスト:\n{task_prompt}"
            ),
            svc=svc,
            history_limit=history_limit,
            use_knowledge=True,
            knowledge_limit=knowledge_limit,
        )

        sandbox = RepoSandbox(doc_path)

        @tool("repo_list")
        def _repo_list(patterns: list[str] | None = None) -> str:
            return sandbox.list_files(patterns=patterns)

        @tool("repo_read")
        def _repo_read(relpath: str, max_bytes: int = 200_000) -> str:
            return sandbox.read_text(relpath, max_bytes=max_bytes)

        @tool("repo_write")
        def _repo_write(relpath: str, content: str) -> str:
            return sandbox.write_text(relpath, content)

        @tool("repo_patch")
        def _repo_patch(unified_diff: str) -> str:
            return sandbox.apply_unified_diff(unified_diff)

        tools = [_repo_list, _repo_read, _repo_write, _repo_patch]
        llm_with_tools = self.llm.bind_tools(tools)

        conversation = messages[:]
        # 過剰実行を防ぐ
        for _ in range(60):
            ai_msg = llm_with_tools.invoke(conversation)
            tool_calls = getattr(ai_msg, "tool_calls", None)
            if not tool_calls:
                # ここで “作業サマリ/注意点” を返す
                text = (ai_msg.content or "").strip()
                return GptResult(content=text, provider="langchain+openai", model_name=self.llm.model_name)

            conversation.append(ai_msg)
            tool_results = []
            for call in tool_calls:
                name = call.get("name");
                args = call.get("args", {}) or {};
                call_id = call.get("id")
                try:
                    if name == "repo_list":
                        result = _repo_list.invoke(args)
                    elif name == "repo_read":
                        result = _repo_read.invoke(args)
                    elif name == "repo_write":
                        result = _repo_write.invoke(args)
                    elif name == "repo_patch":
                        result = _repo_patch.invoke(args)
                    else:
                        result = f"error=unknown tool {name}"
                except Exception as e:
                    result = f"error={type(e).__name__}: {e}"
                tool_results.append(ToolMessage(content=str(result), tool_call_id=call_id))
            conversation.extend(tool_results)

        return GptResult(content="（注意）最大ツール実行回数に達しました。", provider="langchain+openai",
                         model_name=self.llm.model_name)
