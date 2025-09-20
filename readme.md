# SystemGen - Docker/Compose セットアップ

本プロジェクトを Docker で動かし、doc_path はホストの外部フォルダを /docs にマウントして利用します。ポートは 5000、Python は 3.10.11 を使用します。OpenAI API キーは OPENAI_API_KEY 環境変数で渡します。

## 前提
- Docker / Docker Compose が動作する環境
- requirements.txt は UTF-8 で保存済み（自動変換は行いません）
- Git（差分表示に使用）はコンテナ内に同梱済み

## 主要設定
- コンテナ内アプリパス: 環境変数 `PYTHON_PATH`（既定 `/app`）
- 外部ドキュメントパス: コンテナ内 `/docs` にマウント（ホスト側は任意のディレクトリを指定）
- ポート: `5000`（ホスト:コンテナ = 5000:5000）
- OpenAI API キー: 環境変数 `OPENAI_API_KEY`

## 1) .env を用意
## 2) Docker Compose で自動ビルド＆起動（マイグレーション実行）
`.env.example` をコピーして `.env` を作成し、必要項目を設定します。

```
compose はエントリポイントスクリプト（docker-entrypoint.sh）を呼び、Flask-Migrate を用いたマイグレーション（flask db upgrade）を実行してから python app.py を起動します。
cp .env.example .env
# もしくは Windows PowerShell: copy .env.example .env
```

`.env` 設定例
```
OPENAI_API_KEY=sk-xxxxx
# 任意（未指定なら ./docs）
DOCS_HOST_PATH=C:\\work\\docs
# 任意（未指定なら /app）
PYTHON_PATH=/app
# 任意（未指定なら Asia/Tokyo）
TZ=Asia/Tokyo
```

## 2) Docker Compose で自動ビルド＆起動
初回や差分がある場合は自動でビルドが走ります。

```
docker compose up
```

常に再ビルドしたい場合は以下。
```
docker compose up --build
```

停止/削除は以下。
```
docker compose down
```

## 3) アプリへのアクセス
ブラウザで `http://localhost:5000` を開きます。

## 4) doc_path の設定
アプリ画面の「プロジェクト編集」で doc_path にコンテナ内の絶対パスを指定します。
- 例: `/docs/my_project`
- Git リポジトリ（.git が存在）であれば、差分表示は Git の挙動に準拠し、modified と untracked を別タブで表示します。

## 5) よくある質問
- ポート競合: 既に 5000 を使用しているプロセスがある場合、`ports` を `18080:5000` のように変更してください。
- API キー未設定: `OPENAI_API_KEY` が未設定だと AI 機能が動作しません。`.env` または `-e` で渡してください。
- doc_path が反映されない: コンテナ内の絶対パス（/docs/〜）を設定しているか確認してください。
- 差分が出ない: doc_path が Git リポジトリであること（/docs 配下に .git があること）を確認してください。

