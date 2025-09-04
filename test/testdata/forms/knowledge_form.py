from flask_wtf import FlaskForm
from wtforms import StringField, TextAreaField, BooleanField, IntegerField, SubmitField, HiddenField
from wtforms.validators import DataRequired, Optional


class KnowledgeForm(FlaskForm):
    project_id = HiddenField()     # 追加
    knowledge_id = HiddenField()   # 追加（edit時のみ使用）
    title = StringField('タイトル', validators=[DataRequired()])
    category = StringField('カテゴリ', validators=[Optional()])
    content = TextAreaField('内容', validators=[DataRequired()])
    active = BooleanField('有効')
    order = IntegerField('表示順', validators=[Optional()])
    submit = SubmitField('保存')