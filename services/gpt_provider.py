from dataclasses import dataclass
from typing import Optional, Iterator, List, Dict, Any, Tuple, Union

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


class GptProvider(object):
    def __init__(
        self,
        model: str = "gpt-5",
        temperature: float = 0.3,
        timeout: int = 300,
        max_retries: int = 2,
    ):
        self.llm = ChatOpenAI(
            model=model,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )

        self.parser = StrOutputParser()
        self.llm_with_tool = self.llm.bind_tools([
            tools.find_files,
            tools.write_file,
            tools.read_file,
            tools.list_files,
            tools.make_dirs,
            tools.file_stat,
            tools.read_file_range,
            tools.list_python_symbols,
            tools.insert_code,
            tools.update_code,
            tools.delete_code,
            php_tools.php_locate_functions,
            php_tools.php_insert_after_function_end,
            php_tools.php_replace_function_body,
            tools.replace_in_line,
            tools.search_grep,
            tools.detect_txt_encoding_utf8_or_sjis,
            tools.convert_txt_to_utf8,
            # Git tools
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

        # name → tool 関数マップ（invoke で呼び出す）
        self.tool_map = {
            "list_files": tools.list_files,
            "read_file": tools.read_file,
            "write_file": tools.write_file,
            "find_files": tools.find_files,
            "make_dirs": tools.make_dirs,
            "file_stat": tools.file_stat,
            "read_file_range": tools.read_file_range,
            "list_python_symbols": tools.list_python_symbols,
            "insert_code": tools.insert_code,
            "update_code": tools.update_code,
            "delete_code": tools.delete_code,
            "php_locate_functions": php_tools.php_locate_functions,
            "php_insert_after_function_end": php_tools.php_insert_after_function_end,
            "php_replace_function_body": php_tools.php_replace_function_body,
            "replace_in_line": tools.replace_in_line,
            "search_grep": tools.search_grep,
            "detect_txt_encoding_utf8_or_sjis": tools.detect_txt_encoding_utf8_or_sjis,
            "convert_txt_to_utf8": tools.convert_txt_to_utf8,
            # Git tools
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
                "* あなたは有能なソフトウェア設計・開発のアシスタントです。"
                "* 出力は必ず日本語で、Markdown整形（見出し・箇条書き・表など）で分かりやすく書いてください。"
                "* ソースコードの提示は必ず```ソースコード```というように```を使うようにしてください"
                "* あなたはローカル環境で動作しており、バインド済みのツールを使ってファイルシステムにアクセスできます。"
                "* C: などの絶対パスも、ツール（find_files / read_file など）を通じて直接扱えます。"
                "* 『この環境からはアクセスできません』などと回答せず、必ず該当ツールを呼び出してください。"
                "* 与えられたプロンプトにソースコード、または関数名、または、変数名が含まれる場合、ツールでソースを調べるようにしてください。"
                "* ソースコードが見つからない場合は、その旨を回答に含めてください。ユーザーがソースを見て回答したのか、憶測で回答したのかをわかるようにしたいです。"
                "* ファイルを読む前にfile_statでファイルサイズを取得し、大きなファイルの場合はread_file_rangeを使ってください"
            )),
            # ツール利用ガイド（Level 1 対策）
            SystemMessage(content=(
                "ツール利用ガイド:\n"
                "- update_code で anchor を使い複数行の code を置換する場合、必ず length を指定してください（length 未指定はエラー）。\n"
                "- 1 行の属性変更や小さな修正は replace_in_line を優先して使ってください。\n"
                "- 複数行置換では、置換前に read_file_range で該当行を確認し、適用後に search_grep で重複タグ等が残っていないか簡易チェックしてください。\n"
            )),
            SystemMessage(content=(
                "gitに関するガイド:\n"
                "- 差分を求められた場合、特段指示がなければgit_diff_own_changes_filesで差分を取得してください。\n"
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
        history_limit: int = 20,
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
        # 1) ベースメッセージ作成
        messages = self._build_messages(
            project_id, prompt, svc,
            history_limit=history_limit,
            use_knowledge=use_knowledge,
            knowledge_limit=knowledge_limit,
            knowledge_categories=knowledge_categories,
        )

        # 2) ツールを使用した応答生成
        conversation = messages[:]
        final_result = ""
        tool_call_count = 0  # ツール呼び出し回数をカウントする変数
        for _ in range(max_tool_turns):
            ai_msg = self.llm_with_tool.invoke(conversation)
            tool_calls = getattr(ai_msg, "tool_calls", None)

            if not tool_calls:
                # ツール呼び出しがない場合はそのままストリームを返す
                ai = self.llm.invoke(conversation)
                text = (getattr(ai, "content", "") or "").strip()
                for piece in self._chunk_text(text):
                    yield piece
                print("\n[FIN]")
                return

            conversation.append(ai_msg)  # assistant（tool_callsあり）

            latest_tool_messages: List[ToolMessage] = []
            for call in tool_calls:
                name = call.get("name")
                args = call.get("args", {}) or {}
                call_id = call.get("id")
                print(f"{name} {args}")
                try:
                    if name in self.tool_map:
                        result = self.tool_map[name].invoke(args)
                    else:
                        result = f"error=Unknown tool: {name}"
                except Exception as e:
                    result = f"error={type(e).__name__}: {e}"
                    print(e)
                latest_tool_messages.append(ToolMessage(content=str(result), tool_call_id=call_id))
                tool_call_count += 1
                print(f"Tool called {tool_call_count} times")

            conversation.extend(latest_tool_messages)

        # 最大回数に達した場合のみメッセージを出力
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
