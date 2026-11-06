import secrets

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from bladys.models import User, UserRole
from bladys.security import hash_password, verify_password
from bladys.seed_staff import read_passwords, seed_staff, validate_target, main


@pytest.fixture
def seed_environment(monkeypatch):
    settings = {
        "APP_ENV": "development", "BLADYS_ALLOW_DEV_STAFF_SEED": "1",
        "DEV_RECEPTION_PASSWORD": secrets.token_urlsafe(24),
        "DEV_KITCHEN_PASSWORD": secrets.token_urlsafe(24),
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    for name in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(name, raising=False)
    return settings


@pytest.mark.parametrize('database', ['bladys', 'bladys_dev'])
@pytest.mark.parametrize('name', ['APP_ENV', 'BLADYS_ALLOW_DEV_STAFF_SEED'])
def test_seed_requires_explicit_development_opt_in(seed_environment, name, database):
    settings = dict(seed_environment); settings.pop(name)
    with pytest.raises(ValueError):
        validate_target(settings, f'postgresql+psycopg://localhost/{database}')


@pytest.mark.parametrize('url', [None, 'bad-url', 'sqlite://',
    'postgresql://remote/bladys_dev', 'postgresql://remote/bladys',
    'postgresql://localhost/bladys_backup',
    'postgresql://localhost/bladys?host=remote',
    'postgresql://localhost/bladys_production',
    'postgresql://localhost/bladys_dev?host=remote',
    'postgresql://localhost/bladys_dev?hostaddr=10.0.0.1',
    'postgresql://localhost/bladys_dev?service=production',
    'postgresql://localhost/bladys_dev?options=a&options=b',
    'postgresql://localhost/bladys_dev?options=-csearch_path=public%20-cother=value'])
def test_seed_rejects_unsafe_targets(seed_environment, url):
    with pytest.raises(ValueError):
        validate_target(seed_environment, url)


@pytest.mark.parametrize('name', ['PGHOST', 'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'])
def test_seed_rejects_libpq_redirects(seed_environment, name):
    settings = dict(seed_environment, **{name:'external'})
    with pytest.raises(ValueError):
        validate_target(settings, 'postgresql://localhost/bladys_dev')


@pytest.mark.parametrize('url', ['postgresql://localhost/bladys',
    'postgresql+psycopg://127.0.0.1/bladys',
    'postgresql://[::1]/bladys',
    'postgresql://localhost/bladys_dev',
    'postgresql+psycopg://127.0.0.1/bladys_test',
    'postgresql://[::1]/dev_bladys',
    'postgresql://localhost/bladys_test?options=-csearch_path=isolated_schema'])
def test_seed_accepts_explicit_local_targets(seed_environment, url):
    validate_target(seed_environment, url)


@pytest.mark.parametrize('variable', ['DEV_RECEPTION_PASSWORD', 'DEV_KITCHEN_PASSWORD'])
@pytest.mark.parametrize('value', [None, '', '   '])
def test_missing_password_configuration_fails(seed_environment, variable, value):
    settings = dict(seed_environment)
    if value is None: settings.pop(variable)
    else: settings[variable] = value
    with pytest.raises(ValueError, match=variable):
        read_passwords(settings)


def test_postgres_seed_login_authorization_and_repeat(postgres_client, postgres_engine, seed_environment, monkeypatch, capsys):
    """Contexto: se provisionan dos empleados de desarrollo para una prueba manual.
    Garantia: usan autenticacion real y permisos por rol sin duplicar ni sobrescribir credenciales.
    Escenarios: primera ejecucion, repeticion con otras claves, login valido/invalido y accesos prohibidos.
    """
    import bladys.database as database
    monkeypatch.setattr(database, 'engine', postgres_engine)
    monkeypatch.setenv('DATABASE_URL', postgres_engine.url.render_as_string(hide_password=False))
    monkeypatch.setenv('JWT_SECRET_KEY', secrets.token_urlsafe(48))
    main()
    with Session(postgres_engine) as db:
        users = db.scalars(select(User).order_by(User.username)).all()
        assert len(users) == 2
        original = {u.username:(u.id,u.password_hash,u.role,u.is_active) for u in users}
        for u in users:
            variable = 'DEV_RECEPTION_PASSWORD' if u.role == UserRole.RECEPTION else 'DEV_KITCHEN_PASSWORD'
            assert u.password_hash != seed_environment[variable]
            assert verify_password(seed_environment[variable],u.password_hash)
    monkeypatch.setenv('DEV_RECEPTION_PASSWORD', secrets.token_urlsafe(24))
    monkeypatch.setenv('DEV_KITCHEN_PASSWORD', secrets.token_urlsafe(24))
    main()
    with Session(postgres_engine) as db:
        assert {u.username:(u.id,u.password_hash,u.role,u.is_active) for u in db.scalars(select(User))} == original
    client = postgres_client
    tokens = []
    for username, variable in [('reception_test','DEV_RECEPTION_PASSWORD'),('kitchen_test','DEV_KITCHEN_PASSWORD')]:
        response = client.post('/login',json={'username':username,'password':seed_environment[variable]})
        assert response.status_code == 200
        assert response.json()['token_type'] == 'bearer'
        tokens.append(response.json()['access_token'])
        assert client.post('/login',json={'username':username,'password':secrets.token_urlsafe(24)}).status_code == 401
    reception, kitchen = ({'Authorization':'Bearer '+token} for token in tokens)
    assert client.get('/payments/pending',headers=reception).status_code == 200
    assert client.get('/reception/orders/99999',headers=reception).status_code == 404
    assert client.get('/kitchen/orders/pending',headers=kitchen).status_code == 200
    assert client.get('/payments/pending',headers=kitchen).status_code == 403
    assert client.patch('/payments/99999/status',headers=kitchen,json={'status':'APROBADO'}).status_code == 403
    for path in ['/payments/pending','/reception/orders/99999','/payments/99999/proof','/kitchen/orders/pending']:
        assert client.get(path).status_code == 401
    assert client.patch('/payments/99999/status',json={'status':'APROBADO'}).status_code == 401
    output = capsys.readouterr()
    for secret in [*tokens,seed_environment['DEV_RECEPTION_PASSWORD'],seed_environment['DEV_KITCHEN_PASSWORD'],*(v[1] for v in original.values())]:
        assert secret not in output.out + output.err


@pytest.mark.parametrize('conflict', ['role', 'inactive'])
def test_postgres_seed_conflict_rolls_back_all_accounts(postgres_engine, clean_postgres_db, seed_environment, conflict):
    """Contexto: el nombre de cocina ya pertenece a una cuenta incompatible.
    Garantia: no se cambian credenciales, rol ni actividad; tampoco queda recepcion creada parcialmente.
    Escenarios: rol incorrecto o cuenta desactivada, preservando la fila existente.
    """
    password_hash = hash_password(secrets.token_urlsafe(24))
    with Session(postgres_engine) as db, db.begin():
        db.add(User(username='kitchen_test',password_hash=password_hash,
                    role=UserRole.RECEPTION if conflict == 'role' else UserRole.KITCHEN,
                    is_active=conflict != 'inactive'))
    with pytest.raises(ValueError), Session(postgres_engine) as db, db.begin():
        seed_staff(db,read_passwords(seed_environment))
    with Session(postgres_engine) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        user = db.scalar(select(User))
        assert user.username == 'kitchen_test' and user.password_hash == password_hash
        assert user.role == (UserRole.RECEPTION if conflict == 'role' else UserRole.KITCHEN)
        assert user.is_active == (conflict != 'inactive')


@pytest.mark.parametrize('database', ['bladys', 'bladys_dev'])
def test_seed_rejects_production_even_with_local_dev_url(seed_environment, database):
    settings = dict(seed_environment, APP_ENV='production')
    with pytest.raises(ValueError, match='APP_ENV'):
        validate_target(settings,f'postgresql://localhost/{database}')


@pytest.mark.parametrize('variable', ['DEV_RECEPTION_PASSWORD', 'DEV_KITCHEN_PASSWORD'])
def test_command_missing_password_exits_before_writes(seed_environment, monkeypatch, variable):
    import bladys.seed_staff as seeder
    monkeypatch.setattr(seeder, 'load_dotenv', lambda: None)
    monkeypatch.setenv('DATABASE_URL','postgresql://localhost/bladys_dev')
    monkeypatch.delenv(variable, raising=False)
    def unexpected_write(*args):
        pytest.fail('No debe ejecutar la siembra sin configuracion completa')
    monkeypatch.setattr(seeder, 'seed_staff',unexpected_write)
    with pytest.raises(SystemExit, match=variable):
        main()


def test_command_database_errors_do_not_expose_secrets(seed_environment, monkeypatch):
    import bladys.seed_staff as seeder
    import bladys.database as database
    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError
    monkeypatch.setattr(seeder, 'load_dotenv', lambda: None)
    monkeypatch.setenv('DATABASE_URL','postgresql://localhost/bladys_dev')
    # A disposable connection reaches the same command error handler without network access.
    engine = create_engine('sqlite://')
    monkeypatch.setattr(database, 'engine',engine)
    monkeypatch.setattr(seeder, 'validate_target',lambda *args:None)
    def fail(*args):
        raise SQLAlchemyError(seed_environment['DEV_RECEPTION_PASSWORD'])
    monkeypatch.setattr(seeder, 'seed_staff',fail)
    try:
        with pytest.raises(SystemExit) as error:
            main()
        assert 'transaccion revertida' in str(error.value)
        assert seed_environment['DEV_RECEPTION_PASSWORD'] not in str(error.value)
    finally:
        engine.dispose()
