from pathlib import Path
import os
import fnmatch
from langchain_core.tools import tool
from tree_sitter_languages import get_language, get_parser
from tree_sitter import Parser


# PHP用パーサを初期化
PHP_LANGUAGE = get_language("php")
_PHP_PARSER = get_parser('php')


def _ts_parse(src: str):
    return _PHP_PARSER.parse(bytes(src, "utf-8", errors="ignore"))

def _node_text(src_bytes: bytes, node):
    return src_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

def _walk(node):
    # DFS イテレータ
    yield node
    for c in node.children:
        yield from _walk(c)

def _read_file_text(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

@tool
def php_locate_functions(file_path: str, names_json: str) -> str:
    """
    PHPのソースコードを扱うためのツール。指定した関数名の開始/終了行を返す。
    names_json: '["renderFooter","renderHeader"]'
    返り値: {"ok": true, "functions":[{"name":"renderFooter","start":123,"end":161}]}
    """
    import json
    try:
        names = set(json.loads(names_json or "[]"))
    except Exception as e:
        return json.dumps({"ok": False, "error": f"invalid_json: {e}"}, ensure_ascii=False)

    if not Path(file_path).exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)

    src = _read_file_text(file_path)
    tree = _ts_parse(src)
    root = tree.root_node
    src_bytes = src.encode("utf-8", errors="ignore")

    found = []
    for n in _walk(root):
        if n.type == "function_definition":
            # child: name identifier
            # Tree-sitter(PHP)の function_definition 構造に依存
            ident = None
            for c in n.children:
                if c.type == "name":
                    ident = _node_text(src_bytes, c)
                    break
            if ident and ident in names:
                found.append({"name": ident, "start": n.start_point[0] + 1, "end": n.end_point[0] + 1})
    return json.dumps({"ok": True, "functions": found}, ensure_ascii=False)

@tool
def php_insert_after_function_end(file_path: str, target_function: str, code: str) -> str:
    """
    PHPのソースコードを扱うためのツール。指定関数の “終了行の直後” に code を挿入するための edits_json を返す。
    返り値: {"ok": true, "edits":[{"start": <end+1>,"end": <end>,"replacement":"..."}]}
    ※ 挿入は start=end の“空範囲置換”で表現
    """
    import json
    if not Path(file_path).exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)

    src = _read_file_text(file_path)
    tree = _ts_parse(src)
    root = tree.root_node

    src_bytes = src.encode("utf-8", errors="ignore")
    end_line = None
    for n in iter_nodes(root):
        if n.type == "function_definition":
            ident = None
            for c in n.children:
                if c.type == "name":
                    ident = _node_text(src_bytes, c)
                    break
            if ident == target_function:
                end_line = n.end_point[0] + 1
                break

    if end_line is None:
        return json.dumps({"ok": False, "error": "function_not_found"}, ensure_ascii=False)

    # 挿入: start=end_line+1, end=end_line （空範囲に code を挿入）
    # 末尾に改行を保証
    if not code.endswith("\n"):
        code += "\n"

    edits = [{"start": end_line + 1, "end": end_line, "replacement": code}]
    return json.dumps({"ok": True, "edits": edits}, ensure_ascii=False)

@tool
def php_replace_function_body(file_path: str, target_function: str, new_body_php: str) -> str:
    """
    PHPのソースコードを扱うためのツール。指定関数の“本体だけ”を new_body_php に置き換える edits_json を返す。
    new_body_php は波括弧内のステートメント列（例: "echo 'x';\\nreturn $a;"）を想定。
    """
    import json
    if not Path(file_path).exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)

    src = _read_file_text(file_path)
    tree = _ts_parse(src)
    root = tree.root_node
    s_bytes = src.encode("utf-8", errors="ignore")

    for n in _walk(root):
        if n.type == "function_definition":
            ident = None
            for c in n.children:
                if c.type == "name":
                    ident = _node_text(s_bytes, c)
                    break
            if ident == target_function:
                # function本体(block)ノードを取得
                block = None
                for c in n.children:
                    if c.type == "compound_statement":  # { ... }
                        block = c
                        break
                if not block:
                    return json.dumps({"ok": False, "error": "body_not_found"}, ensure_ascii=False)

                # block の { と } の内側行だけを置換
                start_line = block.start_point[0] + 1
                end_line = block.end_point[0] + 1
                # 開始行の '{' の次の行から、終了行の '}' の前の行まで
                body_start = start_line + 1
                body_end = end_line - 1
                if body_start > body_end:
                    body_start = body_end

                # new_body_php をインデント込みで差し込む（末尾改行）
                repl = new_body_php
                if not repl.endswith("\n"):
                    repl += "\n"
                return json.dumps({
                    "ok": True,
                    "edits": [{"start": body_start, "end": body_end, "replacement": repl}]
                }, ensure_ascii=False)

    return json.dumps({"ok": False, "error": "function_not_found"}, ensure_ascii=False)

def iter_nodes(root):
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        for c in reversed(n.children):
            stack.append(c)
