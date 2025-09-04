# controllers/__init__.py
from flask import Flask
from .users_controller import user_bp
from .projects_controller import project_bp
from .docs_controller import docs_bp
from .knowledge_controller import knowledge_bp


def register_blueprints(app: Flask):
    # これはめんどくさいので消したいところ。
    app.register_blueprint(user_bp, url_prefix="/users")
    # プロジェクトを作成する画面
    app.register_blueprint(project_bp, url_prefix="/projects")
    # ドキュメント(設計書)を扱うAIの画面
    app.register_blueprint(docs_bp, url_prefix="/docs")
    # 知識を入力する画面
    app.register_blueprint(knowledge_bp, url_prefix="/knowledge")
