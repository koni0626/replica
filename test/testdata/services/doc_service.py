# services/design_memo_service.py
from typing import List, Optional
from extensions import db
from models.docs import Docs

class DocService:
    def __init__(self):
        pass

    def latest_by_project(self, project_id: int) -> Optional[Docs]:
        return (Docs.query
                .filter(Docs.project_id == project_id)
                .order_by(Docs.committed_at.desc())
                .first())

    # 追加: 件数
    def count_by_project(self, project_id: int) -> int:
        return (Docs.query
                .filter(Docs.project_id == project_id)
                .count())

    # 追加: N件目を取得（0=最新, 1=ひとつ前, ...）
    def nth_by_project(self, project_id: int, n: int) -> Optional[Docs]:
        if n < 0:
            return None
        return (Docs.query
                .filter(Docs.project_id == project_id)
                .order_by(Docs.committed_at.desc())
                .offset(n)
                .limit(1)
                .first())

    def commit(self, *, project_id: int, user_id: Optional[int], prompt: str,
               content: str) -> Docs:
        memo = Docs(
            project_id=project_id,
            user_id=user_id,
            prompt=prompt,
            content=content,
        )
        db.session.add(memo)
        db.session.commit()
        return memo

    def fetch_history(self, project_id: int, limit: Optional[int] = 20, newest_first: bool = False) -> list[Docs]:
        """
        会話履歴として使う Docs をまとめて取得。
        newest_first=False のとき古い→新しいの順で返す（会話再現に便利）
        """
        q = (Docs.query
             .filter(Docs.project_id == project_id)
             .order_by(Docs.committed_at.desc()))
        if limit:
            q = q.limit(limit)
        rows = q.all()  # ここでは新しい→古い

        if newest_first:
            return rows
        # 既定は古い→新しい（会話順に自然）
        return list(reversed(rows))

    def delete_memo(self, project_id: int, memo_id: int) -> bool:
        memo = Docs.query.filter_by(doc_id=memo_id, project_id=project_id).first()
        if not memo:
            return False
        db.session.delete(memo)
        db.session.commit()
        return True
