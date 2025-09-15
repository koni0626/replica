from dataclasses import dataclass
from typing import Optional, Iterator, List, Dict, Any, Tuple, Union
from pathlib import Path
from datetime import datetime
from services.project_service import ProjectService
from services.ai_log import AiRunLogger

# LangChain
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

from services.doc_service import DocService
from models.knowledge import Knowledge
from tools import tools
from tools import php_tools
from tools import git_tool
from tools import network_tool
from tools import rag_tools


class GptProvider(object):
    def __init__(
        self,
        model: str = "gpt-5",
        temperature: float = 0.3,
        timeout: int = 300,
        max_retries: int = 2,
        ai_log_enabled: bool = True,
    ):
        self.llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )
        self.parser = StrOutputParser()
        # AIログ設定
        self.ai_log_enabled = ai_log_enabled

        # ツールのバインド（LLM 側に公開する関数群）
        self.llm_with_tool = self.llm.bind_tools([
            # FS/Text ツール
            tools.find_files,
            tools.write_file,
            tools.read_file,
            tools.list_files,
            tools.list_dirs,
            tools.make_dirs,
            tools.file_stat,
            tools.read_file_range,
            tools.list_python_symbols,
#            tools.insert_code,
#            tools.delete_code,
            tools.search_grep,

            # ここから追加（LLM 編集タグ運用）
            #tools.mark_llm_edit,
            #tools.list_llm_edit_regions,
            #tools.apply_edit_ops,

            # RAG
            rag_tools.rag_index_text,
            rag_tools.rag_update_index,
            rag_tools.rag_build_index,
            rag_tools.rag_query_text,
            # PHP 用
#            php_tools.php_locate_functions,
#            php_tools.php_insert_after_function_end,
#            php_tools.php_replace_function_body,
#            php_tools.php_list_symbols,
#            php_tools.php_detect_namespace_and_uses,
#            php_tools.php_add_strict_types_declare,
#            php_tools.php_insert_use_statement,
#            php_tools.php_add_method_to_class,
#            php_tools.php_replace_method_body,
#            php_tools.php_lint,
            # ネットワーク
            network_tool.fetch_url_text,
            network_tool.fetch_url_links,
            # Git
            git_tool.git_diff_files,
            git_tool.git_diff_patch,
            git_tool.git_list_branches,
            git_tool.git_current_branch,
            git_tool.git_log_range,
            git_tool.git_show_file,
            git_tool.git_status_porcelain,
            git_tool.git_rev_parse,
            git_tool.git_repo_root,
            git_tool.git_diff_own_changes_files,
        ])
        # 検索系ツール（base_path を doc_path 配下に固定する対象）
        self._tools_require_base_path = {"find_files", "list_files", "list_dirs", "search_grep"}
        self.tool_map = {
            # FS/Text ツール
            "list_files": tools.list_files,
            "list_dirs": tools.list_dirs,
            "read_file": tools.read_file,
            "write_file": tools.write_file,
            "find_files": tools.find_files,
            "make_dirs": tools.make_dirs,
            "file_stat": tools.file_stat,
            "read_file_range": tools.read_file_range,
            "list_python_symbols": tools.list_python_symbols,
#            "insert_code": tools.insert_code,
#            "delete_code": tools.delete_code,
            "search_grep": tools.search_grep,

            # ここから追加（LLM 編集タグ運用）
#            "mark_llm_edit": tools.mark_llm_edit,
#            "list_llm_edit_regions": tools.list_llm_edit_regions,
#            "apply_edit_ops": tools.apply_edit_ops,

            # RAG
            "rag_build_index": rag_tools.rag_build_index,
            "rag_update_index": rag_tools.rag_update_index,
            "rag_index_text": rag_tools.rag_index_text,
            "rag_query_text": rag_tools.rag_query_text,
            # PHP 用
#            "php_locate_functions": php_tools.php_locate_functions,
#            "php_insert_after_function_end": php_tools.php_insert_after_function_end,
#            "php_replace_function_body": php_tools.php_replace_function_body,
#            "php_list_symbols": php_tools.php_list_symbols,
#            "php_detect_namespace_and_uses": php_tools.php_detect_namespace_and_uses,
#            "php_add_strict_types_declare": php_tools.php_add_strict_types_declare,
#            "php_insert_use_statement": php_tools.php_insert_use_statement,
#            "php_add_method_to_class": php_tools.php_add_method_to_class,
#            "php_replace_method_body": php_tools.php_replace_method_body,
#            "php_lint": php_tools.php_lint,
            # ネットワーク
            "fetch_url_text": network_tool.fetch_url_text,
            "fetch_url_links": network_tool.fetch_url_links,
            # Git
            "git_diff_files": git_tool.git_diff_files,
            "git_diff_patch": git_tool.git_diff_patch,
            "git_list_branches": git_tool.git_list_branches,
            "git_current_branch": git_tool.git_current_branch,
            "git_log_range": git_tool.git_log_range,
            "git_show_file": git_tool.git_show_file,
            "git_status_porcelain": git_tool.git_status_porcelain,
            "git_rev_parse": git_tool.git_rev_parse,
            "git_repo_root": git_tool.git_repo_root,
            "git_diff_own_changes_files": git_tool.git_diff_own_changes_files,
        }

    def _project_base_dir(self, project_id: int) -> Path:
        """project_id から doc_path を解決し、存在するディレクトリ Path を返す。"""
        ps = ProjectService()
        proj = ps.fetch_by_id(project_id)
        if not proj or not getattr(proj, "doc_path", None):
            raise ValueError("doc_path_not_set")
        base = Path(proj.doc_path).expanduser().resolve()
        if (not base.exists()) or (not base.is_dir()):
            raise ValueError("invalid_doc_path")
        return base

    def _resolve_search_base(self, base_dir: Path, requested: Optional[str]) -> Path:
        """検索用の base_path を doc_path 配下に制限しつつ、サブディレクトリ指定を許容する。
        受け取り例:
        - None / 空文字 / "/" → base_dir
        - "/docs/src" / "docs/src" / "src" / "/src" → base_dir/src
        安全対策:
        - コロン（Windowsドライブ）や .. を含むパスは拒否し base_dir を返す
        - resolve() 後に base_dir 配下であることを relative_to で検証
        - ディレクトリが存在しない場合は base_dir にフォールバック
        """
        s = (requested or "").strip()
        if not s:
            return base_dir
        s = s.replace("\\", "/")
        """
        # 先頭スラッシュを除去（絶対パス化の防止）
        if s.startswith("/"):
            s = s[1:]
        # 先頭の"docs/"はルート表現として扱い、取り除く
        if s.lower().startswith("docs/"):
            s = s[5:]
        """
        # 危険な表現を拒否
        if ":" in s:
            return base_dir
        parts = [p for p in s.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            return base_dir
        sub = (base_dir / "/".join(parts)).resolve()
        try:
            # base_dir 配下であることを確認
            sub.relative_to(base_dir)
        except Exception:
            return base_dir
        if sub.exists() and sub.is_dir():
            return sub
        return base_dir

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
        messages: List[Any] = [
            SystemMessage(content=(
                "# 役割\n"
                "* あなたは有能なソフトウェア設計・開発のアシスタントです。\n"
                f"* ツールを使用する際に使用するproject_idは{project_id}を使ってください。\n"
                "* 最終出力は必ず日本語で、Markdown整形（見出し・箇条書き・表など）で分かりやすく書いてください。\n"
                "* あなたはローカル環境で動作しており、バインド済みのツールを使ってファイルシステムにアクセスできます。\n"
                "* C: などの絶対パスも、ツール（find_files / read_file など）を通じて直接扱えます。\n"
                "* 『この環境からはアクセスできません』などと回答せず、必ず該当ツールを呼び出してください。\n"
                "* 与えられたプロンプトにソースコード、または関数名、または、変数名が含まれる場合、ツールでソースを調べるようにしてください。\n"
                "* ソースコードが見つからない場合は、その旨を回答に含めてください。ユーザーがソースを見て回答したのか、憶測で回答したのかをわかるようにしたいです。\n"
                "* ファイルを読む前にfile_statでファイルサイズを取得し、大きなファイルの場合はread_file_rangeを使ってください\n"
            )),
            SystemMessage(content=(
                "# gitに関するガイド:\n"
                "- 差分を求められた場合、特段指示がなければgit_diff_own_changes_filesで差分を取得してください。\n"
            )),

            SystemMessage(content=(
                "# ソースコード修正時のガイド:\n"
                "- 500行以下のファイルの場合、新しくソースプログラムをwrite_fileで書き換える。\n"
                "- 500行より大きいファイルの場合、ソースは変更せず、修正が必要な分をメッセージで表示する\n"
            )),
        ]

        if use_knowledge and Knowledge is not None:
            kn = self._fetch_knowledge(project_id=project_id, limit=knowledge_limit, categories=knowledge_categories)
            if kn:
                messages.append(SystemMessage(content=(
                    "以下はプロジェクトのナレッジベース（Markdown）です。"
                    "この内容を最優先で尊重して回答してください。\n\n" + kn
                )))

        # 過去のやり取りを戻す
        history = svc.fetch_history(project_id=project_id, limit=history_limit, newest_first=False)
        for memo in history:
            if getattr(memo, "prompt", None):
                messages.append(HumanMessage(content=memo.prompt))
            if getattr(memo, "content", None):
                messages.append(AIMessage(content=memo.content))
        messages.append(HumanMessage(content=new_prompt))
        return messages

    def _debug_print_messages(self, messages: List[Any], head: str = "") -> None:
        """メッセージ配列をデバッグ出力する（内容は長すぎる場合は一部省略）。"""
        try:
            if head:
                print(head)
            for i, m in enumerate(messages, 1):
                role = getattr(m, "type", None) or m.__class__.__name__
                content = getattr(m, "content", "")
                if isinstance(content, str):
                    s = content.replace("\n", "\\n")
                    if len(s) > 300:
                        s = s[:300] + "...(truncated)"
                else:
                    s = str(content)
                print(f"[{i:02d}] {role}: {s}")
            print("-" * 60)
        except Exception as e:
            print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] _debug_print_messages error: {e}")

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

    def stream_with_history_and_tool(
        self,
        *,
        project_id: int,
        prompt: str,
        svc: DocService,
        history_limit: int = 10,
        use_knowledge: bool = True,
        knowledge_limit: int = 8,
        knowledge_categories: Optional[List[str]] = None,
        max_tool_turns: int = 200,
    ):
        """
        履歴+ナレッジを注入してストリーミングで応答を返す。
        ツールを使用してファイルシステムにアクセスし、必要な操作を行う。

        戻り値: ジェネレータ（strの断片を yield）
        """
        # ロガー初期化
        logger = AiRunLogger(project_id, enabled=True) if getattr(self, 'ai_log_enabled', True) else AiRunLogger(project_id, enabled=False)
        try:
            logger.start_session({
                "prompt": prompt,
                "history_limit": history_limit,
                "use_knowledge": use_knowledge,
                "knowledge_limit": knowledge_limit,
                "knowledge_categories": knowledge_categories,
                "max_tool_turns": max_tool_turns,
            })
        except Exception as _e:
            # ロガー初期化に失敗しても処理は継続
            print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] logger init failed: {_e}")

        # 1) ベースメッセージ作成
        messages = self._build_messages(
            project_id, prompt, svc,
            history_limit=history_limit,
            use_knowledge=use_knowledge,
            knowledge_limit=knowledge_limit,
            knowledge_categories=knowledge_categories,
        )

        # DEBUG: 入力時点のメッセージを出力
        try:
            logger.messages_initial(messages)
        except Exception:
            pass
        self._debug_print_messages(messages, head=f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] Initial conversation (with system/history/prompt)")

        # 2) ツールを使用した応答生成
        conversation = messages[:]
        tool_call_count = 0  # ツール呼び出し回数をカウント
        turn = 1  # デバッグ用: ループターン番号

        # doc_path（= base_path）を1度だけ解決しておく
        try:
            base_dir = self._project_base_dir(project_id)
        except Exception:
            yield "（エラー）doc_path が未設定または無効です。プロジェクト詳細で doc_path を設定してください。"
            try:
                logger.end_session(status="error", summary="doc_path invalid")
                logger.close()
            except Exception:
                pass
            return

        for _ in range(max_tool_turns):
            try:
                logger.turn_start(turn, conversation_len=len(conversation))
            except Exception:
                pass
            print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] === TURN {turn} START ===")

            ai_msg = self.llm_with_tool.invoke(conversation)
            tool_calls = getattr(ai_msg, "tool_calls", None)
            try:
                logger.ai_raw(turn, getattr(ai_msg, "content", ""), tool_calls_preview=tool_calls)
            except Exception:
                pass

            if not tool_calls:
                # ツール呼び出しがない場合はそのままストリームを返す
                text = (getattr(ai_msg, "content", "") or "")
                # DEBUG: 最終応答テキスト
                try:
                    prev = (text or "").replace("\n", "\\n")
                    if len(prev) > 500:
                        prev = prev[:500] + "...(truncated)"
                    print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] Final AI content: {prev}")
                except Exception as e:
                    print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] print final content failed: {e}")
                try:
                    logger.final_text(text)
                    logger.end_session(status="ok", summary=f"turns={turn-1}, tools={tool_call_count}")
                    logger.close()
                except Exception:
                    pass
                yield text
                print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] === TURN {turn} END (no tools) ===")
                print("\n[FIN]")
                return

            conversation.append(ai_msg)  # assistant（tool_callsあり）

            # DEBUG: このターンのAI生出力（tool_calls あり）
            try:
                preview = getattr(ai_msg, "content", "") or ""
                if isinstance(preview, str) and len(preview) > 200:
                    preview = preview[:200] + "...(truncated)"
                print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] AI(tool_calls) content: {preview}")
                print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] tool_calls: {tool_calls}")
            except Exception as e:
                print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] print ai_msg failed: {e}")

            latest_tool_messages: List[ToolMessage] = []
            for call in tool_calls:
                name = call.get("name")
                args = call.get("args", {}) or {}
                call_id = call.get("id")
                # ログ: ツール呼び出し
                try:
                    logger.tool_call(turn, name, args, call_id)
                except Exception:
                    pass

                print(f"{name} {args}")
                print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] Call tool: {name} args={args}")
                try:
                    # 検索系ツールなら base_path を doc_path に強制上書き
                    if name in self._tools_require_base_path:
                        args = dict(args)
                        requested = args.get("base_path") or args.get("path") or args.get("start_dir")
                        search_base = self._resolve_search_base(base_dir, requested)
                        args["base_path"] = str(search_base)
                    if name in self.tool_map:
                        _tool = self.tool_map[name]
                        if hasattr(_tool, "invoke"):
                            result = _tool.invoke(args)  # LangChain Tool
                        else:
                            result = _tool(**args)  # 生の関数
                    else:
                        result = f"error=Unknown tool: {name}"
                except Exception as e:
                    result = f"error={type(e).__name__}: {e}"
                    print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}]", e)

                latest_tool_messages.append(ToolMessage(content=str(result), tool_call_id=call_id))
                try:
                    logger.tool_result(turn, call_id, result)
                except Exception:
                    pass
                tool_call_count += 1
                print(f"Tool called {tool_call_count} times")

            conversation.extend(latest_tool_messages)

            # DEBUG: ツール実行結果（ToolMessage）もコンソール出力
            try:
                for tm in latest_tool_messages:
                    c = getattr(tm, "content", "")
                    s = (c[:300] + "...(truncated)") if isinstance(c, str) and len(c) > 300 else c
                    print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] ToolMessage -> {s}")
            except Exception as e:
                print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] print tool messages failed: {e}")

            # ターン終了（ツール実行ありのケース）
            try:
                print(f"[{datetime.now().strftime('%Y/%m/%d %H:%M:%S')}] [DEBUG] === TURN {turn} END ===")
            except Exception:
                pass
            turn += 1

        # 最大回数に達した場合のみメッセージを出力
        try:
            logger.end_session(status="max_turns", summary=f"turns~{turn}, tools={tool_call_count}")
            logger.close()
        except Exception:
            pass
        if tool_call_count >= max_tool_turns:
            yield "（注意）最大ツール実行回数に達しました。"
    # 補助：長文を安全に分割して流す（句読点と改行で優先分割しつつ、最大長でフォールバック）
    def _chunk_text(self, text: str, chunk_size: int = 800) -> List[str]:
        import re
        # まずは文区切りで粗く分割
        sentences = re.split(r'(?<=[。．！？!?])\s*', text)
        out: List[str] = []
        buf = ""
        for s in sentences:
            if not s:
                continue
            if len(buf) + len(s) <= chunk_size:
                buf += s
            else:
                if buf:
                    out.append(buf)
                if len(s) <= chunk_size:
                    out.append(s)
                    buf = ""
                else:
                    # 1文が長すぎる場合は強制分割
                    for i in range(0, len(s), chunk_size):
                        out.append(s[i:i + chunk_size])
                    buf = ""
        if buf:
            out.append(buf)
        return out
