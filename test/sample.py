from pathlib import Path
import json
import os
from typing import List, Dict, Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool

@tool
def find_files(base_path: str, pattern: str = "**/*", max_files: int = 2000) -> str:
    """
    base_path配下から、globパターンでファイルを検索し、
    base_pathからの相対パスを改行区切りの文字列で返します。

    例:
      find_files("repo", "**/*.py")  ->  "app/main.py\nutils/io.py\n..."
      find_files("repo", "templates/**/*.html")

    Args:
        base_path: 検索の起点ディレクトリ
        pattern:  globパターン（例: "**/*.py", "src/**/*.php" など）
        max_files: 返す最大件数（過大応答の抑制）

    Returns:
        見つかった相対パスの改行区切り文字列（0件でも空文字列）
    """
    root = Path(base_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        return ""

    matched = []
    for p in root.glob(pattern):
        if p.is_file():
            matched.append(p.relative_to(root).as_posix())
            if len(matched) >= max_files:
                break
    matched.sort()
    return "\n".join(matched)


@tool
def read_file(file_name: str) -> str:
    """
    引き数に指定されたファイルの内容を読み取り、テキストで返却する。
    """
    with open(file_name, encoding="utf-8") as f:
        text = f.read()

    return text

@tool
def write_file(file_path: str, content: str) -> bool:
    """
    第1引数に指定されたファイルに、指定された文字列を書き込みます。
    ディレクトリが無い場合は、作成します。

    :param file_path: ファイルのパス
    :param content: ファイルに書き込む文字列
    :return: 正常時True, 異常時Falseを返す
    """
    try:
        # 親ディレクトリを作成（存在してもエラーにならない）
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # ファイルに書き込み（上書きモード）
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)

        return True
    except Exception as e:
        # ログ出力なども適宜ここで行う
        print(f"Error writing file {file_path}: {e}")
        return False


@tool
def make_dirs(dir_path: str) -> bool:
    """
    指定ディレクトリを作成します（親ディレクトリもまとめて作成）。
    既に存在する場合も True を返します。

    Args:
        dir_path: 作成したいディレクトリのパス

    Returns:
        正常時 True、失敗時 False
    """
    try:
        os.makedirs(dir_path, exist_ok=True)
        return True
    except Exception as e:
        print(f"[make_dirs] error: {e}")
        return False


@tool
def light_control(degrees: float) -> bool:
    """
    ライトを右にdegrees度回します。
    """
    print(f"light_control: {degrees}")
    return True

@tool
def list_files(base_path: str) -> str:
    """
    base_path配下の全ファイルの相対パスを、改行区切りの文字列で返す。
    例:
      "dir/a.txt\nsrc/main.py\n..."

    Args:
        base_path: 起点ディレクトリ（相対/絶対どちらでも可）

    Raises:
        ValueError: base_path が存在しない or ディレクトリでない場合
    """
    root = Path(base_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"base_path がディレクトリとして存在しません: {base_path}")

    # 再帰で全ファイルを収集（隠しファイルも含む）
    paths = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix in {".py", ".html", ".php"}:
            rel = p.relative_to(root).as_posix()
            paths.append(rel)

    # 安定化のためソートしてから改行結合
    print(paths)
    return "\n".join(sorted(paths))


def main():
    llm = ChatOpenAI(model="gpt-5")

    llm_with_tool = llm.bind_tools([light_control, list_files, read_file])

    tool_map = {
        "light_control": light_control,
        "list_files": list_files,
        "read_file": read_file,
        "write_file": write_file,
        "find_files": find_files,
        "make_dirs": make_dirs,
    }

    messages = [
        SystemMessage("あなたはリポジトリのファイル一覧を手伝うAIです。"),
        HumanMessage(r"C:\Users\konishi\PycharmProjects\SystemGen2の下にあるファイル一覧を教えてください"),
        HumanMessage(r"取得したファイルのファイルサイズをすべて教えてください"),
    ]

    turn = 0
    while True:
        turn += 1
        response = llm_with_tool.invoke(messages)
        print(f"======= turn {turn} / model says =======")
        print("content:", repr(response.content))
        print("tool_calls:", response.tool_calls)

        messages.append(response)
        if not response.tool_calls:
            # これで最終回答（テキスト）が得られた
            break

        # すべてのtool_callを実行してToolMessageを追加
        for call in response.tool_calls:
            tool = tool_map.get(call["name"])
            if not tool:
                # 未登録ツールの防御
                messages.append(ToolMessage(content=f"[unknown tool] {call['name']}", tool_call_id=call["id"]))
                continue

            # 引数でツール実行
            value = tool.invoke(call["args"])

            # ToolMessage.content は必ず文字列で渡す
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)

            # OpenAI 仕様に合わせ tool_call_id を必ず付与
            messages.append(ToolMessage(content=value, tool_call_id=call["id"]))

    print("====== FINAL ======")
    print(response.content)



if __name__ == '__main__':
    main()

