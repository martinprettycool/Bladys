"""Browser-test server: actual FastAPI catalog on an isolated in-memory database."""
import os
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["ORDER_ACCESS_SECRET_KEY"] = "browser-test-only-order-capability-secret"
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from bladys.main import app
from bladys.database import get_session
from bladys.models import Base
from bladys.seed_menu import seed_menu
import uvicorn
test_url = os.environ.get("BLADYS_BROWSER_DATABASE_URL")
if test_url:
    schema = os.environ["BLADYS_BROWSER_SCHEMA"]
    if not schema.startswith("bladys_browser_") or not schema.replace("_", "").isalnum():
        raise RuntimeError("Invalid isolated test schema")
    engine = create_engine(test_url, connect_args={"options": f"-csearch_path={schema}"})
else:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
Base.metadata.create_all(engine)
with Session(engine) as session:
    seed_menu(session)
    session.commit()
def session_override():
    with Session(engine) as session:
        yield session
app.dependency_overrides[get_session] = session_override
uvicorn.run(app, host="127.0.0.1", port=8174, log_level="warning")
