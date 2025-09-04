# models/__init__.py
# ここで各テーブルのクラスをimportしておくと、db.create_all()で作成される
from extensions import db

def load_models():
    # ここにモデルモジュールを追加
    from .users import Users  # noqa: F401
    from .projects import Projects
    from .docs import Docs # ★追加