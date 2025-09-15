from pathlib import Path
import os
from typing import Optional, List, Dict
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

# ============ 追加ツール: 解析・小規模編集（PHP） ============
def _find_class_node_by_name(root, src_bytes: bytes, class_name: str):
    for n in _walk(root):
        if n.type == "class_declaration":
            # class_declaration -> name child
            ident = None
            for c in n.children:
                if c.type == "name":
                    ident = _node_text(src_bytes, c)
                    break
            if ident == class_name:
                return n
    return None


@tool
def php_list_symbols(file_path: str) -> str:
    """
    ファイル内のトップレベル関数/クラス/メソッドを列挙して返す。
    戻り: {"ok":true, "symbols":[{"kind":"class|function|method","name":"...","class":null|"Foo","visibility":"public|protected|private|null","static":bool,"start":int,"end":int}]}
    """
    import json, re
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)
    src = _read_file_text(file_path)
    tree = _ts_parse(src)
    root = tree.root_node
    b = src.encode("utf-8", errors="ignore")
    out = []
    for n in _walk(root):
        if n.type == "function_definition":
            ident = None
            for c in n.children:
                if c.type == "name":
                    ident = _node_text(b, c)
                    break
            if ident:
                out.append({
                    "kind": "function",
                    "name": ident,
                    "class": None,
                    "visibility": None,
                    "static": False,
                    "start": n.start_point[0]+1,
                    "end": n.end_point[0]+1,
                })
        elif n.type == "class_declaration":
            cname = None
            for c in n.children:
                if c.type == "name":
                    cname = _node_text(b, c)
                    break
            if cname:
                out.append({
                    "kind": "class",
                    "name": cname,
                    "class": None,
                    "visibility": None,
                    "static": False,
                    "start": n.start_point[0]+1,
                    "end": n.end_point[0]+1,
                })
        elif n.type == "method_declaration":
            mname = None
            for c in n.children:
                if c.type == "name":
                    mname = _node_text(b, c)
                    break
            if not mname:
                continue
            # 祖先に属するクラス名を簡易特定
            cls = None
            pnode = n.parent
            while pnode is not None:
                if pnode.type == "class_declaration":
                    for cc in pnode.children:
                        if cc.type == "name":
                            cls = _node_text(b, cc)
                            break
                    break
                pnode = pnode.parent
            # 可視性/static をソース断片から簡易抽出
            head_text = _node_text(b, n)[:200]  # ノード先頭側の断片
            vis = None
            if re.search(r"\bpublic\b", head_text): vis = "public"
            elif re.search(r"\bprotected\b", head_text): vis = "protected"
            elif re.search(r"\bprivate\b", head_text): vis = "private"
            is_static = bool(re.search(r"\bstatic\b", head_text))
            out.append({
                "kind": "method",
                "name": mname,
                "class": cls,
                "visibility": vis,
                "static": is_static,
                "start": n.start_point[0]+1,
                "end": n.end_point[0]+1,
            })
    return json.dumps({"ok": True, "symbols": out}, ensure_ascii=False)


@tool
def php_detect_namespace_and_uses(file_path: str) -> str:
    """
    namespace、トップレベルuse文、declare(strict_types=1)の有無を検出。
    戻り: {"ok":true, "namespace":"App\\Foo"|null, "uses":[{"fqcn":"A\\B","alias":"C"}], "strict_types": true/false}
    """
    import json, re
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)
    src = _read_file_text(file_path)
    tree = _ts_parse(src)
    root = tree.root_node
    b = src.encode("utf-8", errors="ignore")

    ns = None
    uses = []
    strict = bool(re.search(r"declare\s*\(\s*strict_types\s*=\s*1\s*\)\s*;", src))

    # ASTから取得（なければ簡易正規表現）
    for n in root.children:
        if n.type == "namespace_definition":
            # namespace Foo\Bar; の名前抽出
            text = _node_text(b, n)
            m = re.search(r"namespace\s+([^;{]+)", text)
            if m:
                ns = m.group(1).strip()
        elif n.type in ("namespace_use_declaration", "use_declaration"):
            text = _node_text(b, n)
            # use A\B as C; or use A\B; も対応
            for line in text.split(";"):
                line = line.strip()
                if not line.startswith("use "):
                    continue
                body = line[4:].strip()
                # カンマ区切りの複数useにも対応（use A\B, C\D as E;）
                parts = [s.strip() for s in body.split(",") if s.strip()]
                for part in parts:
                    m2 = re.match(r"([^\s]+)\s+as\s+([A-Za-z_][A-Za-z0-9_]*)$", part)
                    if m2:
                        uses.append({"fqcn": m2.group(1), "alias": m2.group(2)})
                    else:
                        uses.append({"fqcn": part, "alias": None})
        # 先頭領域のみで十分
        if n.start_point[0] > 200:
            break

    return json.dumps({"ok": True, "namespace": ns, "uses": uses, "strict_types": strict}, ensure_ascii=False)


@tool
def php_add_strict_types_declare(file_path: str) -> str:
    """
    declare(strict_types=1); が無ければ先頭（<?php の直後）に追加する edits を返す。
    既にある場合は note=already_present。
    """
    import json, re
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)
    src = _read_file_text(file_path)
    if re.search(r"declare\s*\(\s*strict_types\s*=\s*1\s*\)\s*;", src):
        return json.dumps({"ok": True, "edits": [], "note": "already_present"}, ensure_ascii=False)

    lines = src.splitlines(True)
    insert_at = 0
    # <?php の直後に挿入（shebangやBOM考慮簡易）
    for i, ln in enumerate(lines[:5]):
        if "<?php" in ln:
            insert_at = i + 1
            break
    code = "declare(strict_types=1);\n"
    edits = [{"start": insert_at+1, "end": insert_at, "replacement": code}]
    return json.dumps({"ok": True, "edits": edits}, ensure_ascii=False)


@tool
def php_insert_use_statement(file_path: str, fqcn: str, alias: Optional[str] = None) -> str:
    """
    トップレベルの use 宣言を適切な位置（namespace直下の既存use群の末尾）に追加する edits を返す。
    既に同一の use が存在する場合は no-op。
    """
    import json, re
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)
    src = _read_file_text(file_path)

    # 既存重複チェック（簡易）
    pat_same = re.compile(rf"^\s*use\s+{re.escape(fqcn)}\s*(?:;|\s+as\s+{re.escape(alias or '')}\s*;)\s*$", re.IGNORECASE | re.MULTILINE)
    if pat_same.search(src):
        return json.dumps({"ok": True, "edits": [], "note": "already_present"}, ensure_ascii=False)

    lines = src.splitlines(True)
    # namespace/end of namespace/use の最後の行を探す
    ns_line = -1
    last_use_line = -1
    for i, ln in enumerate(lines[:400]):
        if ns_line == -1 and ln.strip().startswith("namespace "):
            ns_line = i
        if ln.strip().startswith("use ") and (ns_line == -1 or i > ns_line):
            last_use_line = i
        # 早期終了（class や function が始まったら打ち切り）
        if any(kw in ln for kw in ("class ", "interface ", "trait ", "function ")) and i > ns_line >= 0:
            break
    insert_at = (last_use_line if last_use_line != -1 else (ns_line if ns_line != -1 else 0)) + 1
    stmt = f"use {fqcn}"
    if alias:
        stmt += f" as {alias}"
    stmt += ";\n"
    edits = [{"start": insert_at+1, "end": insert_at, "replacement": stmt}]
    return json.dumps({"ok": True, "edits": edits}, ensure_ascii=False)


@tool
def php_add_method_to_class(file_path: str, class_name: str, method_php_code: str, where: str = "end") -> str:
    """
    指定クラスの終端（'}'直前）や指定位置にメソッドを追加する edits を返す。
    method_php_code は "public function foo() { ... }" のような完全なメソッド宣言を想定。
    """
    import json
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)
    src = _read_file_text(file_path)
    tree = _ts_parse(src)
    root = tree.root_node
    b = src.encode("utf-8", errors="ignore")

    cls = _find_class_node_by_name(root, b, class_name)
    if not cls:
        return json.dumps({"ok": False, "error": "class_not_found"}, ensure_ascii=False)

    # クラスの '}' 直前に挿入
    end_line = cls.end_point[0] + 1
    insert_line = end_line  # 空範囲挿入: start=end=end_line-1+1

    code = method_php_code
    if not code.endswith("\n"):
        code += "\n"
    # クラス内の適当なインデント（先頭メンバの行頭空白を参考にする簡易実装）
    lines = src.splitlines(True)
    indent = "    "
    for i in range(cls.start_point[0]+1, min(cls.end_point[0], cls.start_point[0]+30)):
        line = lines[i] if i < len(lines) else ""
        if line.strip():
            indent = line[:len(line)-len(line.lstrip())]
            break
    if not code.startswith("\n"):
        code = "\n" + code
    if not code.endswith("\n\n"):
        code = code + "\n"
    code = code.replace("\n", "\n" + indent)
    code = code.replace("\n"+indent+"\n", "\n\n")  # 余分なインデント空行調整

    edits = [{"start": insert_line, "end": insert_line-1, "replacement": code}]
    return json.dumps({"ok": True, "edits": edits}, ensure_ascii=False)


@tool
def php_replace_method_body(file_path: str, class_name: str, method_name: str, new_body_php: str) -> str:
    """
    指定クラスの指定メソッドの「本体（{ ... }内のみ）」を new_body_php で置換する edits を返す。
    new_body_php はステートメント列（末尾にセミコロンを含む）を想定。
    """
    import json
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)

    src = _read_file_text(file_path)
    tree = _ts_parse(src)
    root = tree.root_node
    b = src.encode("utf-8", errors="ignore")

    cls = _find_class_node_by_name(root, b, class_name)
    if not cls:
        return json.dumps({"ok": False, "error": "class_not_found"}, ensure_ascii=False)

    target = None
    for n in _walk(cls):
        if n.type == "method_declaration":
            mname = None
            for c in n.children:
                if c.type == "name":
                    mname = _node_text(b, c)
                    break
            if mname == method_name:
                target = n
                break
    if not target:
        return json.dumps({"ok": False, "error": "method_not_found"}, ensure_ascii=False)

    block = None
    for c in target.children:
        if c.type == "compound_statement":
            block = c
            break
    if not block:
        return json.dumps({"ok": False, "error": "body_not_found"}, ensure_ascii=False)

    start_line = block.start_point[0] + 1
    end_line = block.end_point[0] + 1
    body_start = start_line + 1
    body_end = end_line - 1
    if body_start > body_end:
        body_start = body_end

    repl = new_body_php
    if not repl.endswith("\n"):
        repl += "\n"

    return json.dumps({"ok": True, "edits": [{"start": body_start, "end": body_end, "replacement": repl}]}, ensure_ascii=False)


@tool
def php_lint(file_path: str) -> str:
    """
    php -l を実行して構文チェック。戻り: {ok, passed, stdout, stderr, exit_code}
    PHPが未インストール環境では ok=false, error=... を返す。
    """
    import json, subprocess, shutil
    p = Path(file_path)
    if not p.exists():
        return json.dumps({"ok": False, "error": "file_not_found"}, ensure_ascii=False)
    php_bin = shutil.which("php")
    if not php_bin:
        return json.dumps({"ok": False, "error": "php_not_found"}, ensure_ascii=False)
    try:
        proc = subprocess.run([php_bin, "-l", file_path], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        passed = (proc.returncode == 0)
        return json.dumps({
            "ok": True,
            "passed": bool(passed),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": int(proc.returncode),
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": f"exec_error: {e}"}, ensure_ascii=False)
