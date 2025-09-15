from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_migrate import Migrate
from flask_session import Session

db = SQLAlchemy()
csrf = CSRFProtect()
migrate = Migrate()
server_session = Session()
