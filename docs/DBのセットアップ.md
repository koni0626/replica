はい、\*\*Flask-Migrate（Alembic）\*\*でDBを作れます。下記の手順でOKです。

## 1) 追加インストール

```bash
pip install flask-migrate
```

## 2) コードにMigrateを組み込み

### `extensions.py`

```python
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_migrate import Migrate

db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()
```

### `app.py`

```python
from flask import Flask
from config import Config
from extensions import db, csrf, migrate
from controllers import register_blueprints

def create_app():
    app = Flask(__name__, template_folder="templates")
    app.config.from_object(Config)

    db.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    # モデル読み込み（重要：自動検出のため）
    from models import load_models
    load_models()

    register_blueprints(app)
    return app
```

> ⚠️ 以前の `with app.app_context(): db.create_all()` は不要です（マイグレーションに統一）。

## 3) マイグレーション実行コマンド

アプリファクトリ方式なので `FLASK_APP` は **`app:create_app`** を指定します。

### PowerShell（Windows）

```powershell
$env:FLASK_APP = "app:create_app"
flask db init
flask db migrate -m "init schema"
flask db upgrade
```

### bash（macOS/Linux）

```bash
export FLASK_APP="app:create_app"
flask db init
flask db migrate -m "init schema"
flask db upgrade
```

* `flask db init` … `migrations/` が作成されます
* `flask db migrate` … モデル定義（`models/*.py`）から差分を自動検出してスクリプト生成
* `flask db upgrade` … 実DBに反映（SQLiteなら `sample.db` がここで作成されます）

## 4) うまくいかないときのチェック

* **メタデータが見つからない**系エラー
  → `create_app()` の中で必ず `from models import load_models; load_models()` を呼んでいるか確認。
* **モデルが反映されない**
  → `models/__init__.py` の `load_models()` に、作成した各テーブルのモデルを `import` しているか確認。例：

  ```python
  def load_models():
      from .user import User  # 追加したモデルを並べる
  ```
* **同名のテーブル/制約で失敗**
  → 既存のDBが壊れている場合は一度バックアップし、`sample.db` を削除して再度 `upgrade` を実行。

## 5) 以後の変更フロー

モデルを修正 →

```bash
flask db migrate -m "init db"
flask db upgrade
```

必要なら、`requirements.txt` に `Flask-Migrate` も追記しておくとチーム運用が楽です。
詰まったログやエラーがあれば貼ってください。こちらで原因を特定します。
