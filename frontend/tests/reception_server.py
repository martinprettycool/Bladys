"""Real API with disposable SQLite data and test-only staff credentials."""
import os
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['JWT_SECRET_KEY'] = 'reception-browser-test-only-secret-123456789'
os.environ['ORDER_ACCESS_SECRET_KEY'] = 'reception-browser-order-only-secret-123456789'
from tempfile import TemporaryDirectory
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from bladys.models import Base, User, UserRole
from bladys.security import hash_password
from bladys.seed_menu import seed_menu
from bladys.main import app
from bladys.database import get_session
import uvicorn
engine = create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
Base.metadata.create_all(engine)
with Session(engine) as session:
    seed_menu(session)
    for role in (UserRole.RECEPTION,UserRole.KITCHEN):
        session.add(User(username=role.value,password_hash=hash_password('browser-test-password'),role=role))
    session.commit()
def session_override():
    with Session(engine) as session:
        yield session
app.dependency_overrides[get_session] = session_override
with TemporaryDirectory(prefix='bladys-reception-') as directory:
    os.chdir(directory)
    uvicorn.run(app,host='127.0.0.1',port=8176,log_level='warning')
