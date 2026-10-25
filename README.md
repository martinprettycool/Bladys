# Blady's Pizza

Challenge 1A: solo modelo de datos, sin endpoints.

## Preparar
```powershell
uv sync
$env:DATABASE_URL = "postgresql+psycopg://usuario:password@localhost:5432/bladys"
uv run alembic upgrade head
```

Usar una base PostgreSQL nueva y vacia. La revision inicial local sustituye
la implementacion anterior; no aplicar sobre una base con la antigua revision 0001.
No se ha modificado ninguna base existente.

Modelos: src/bladys/models.py. Migracion: alembic/versions/0001_initial.py.
Importes: Decimal / Numeric(12, 2), hasta 9999999999.99, dos decimales.
Solo description admite NULL. Estado inicial: PENDIENTE_ACEPTACION.
Las FK RESTRICT preservan referencias al borrar productos o pedidos.
is_available=false retira un producto del menu sin borrar su historial.
unit_price almacena el precio historico; copiarlo y calcular total queda para otro paso.
Los defaults se aplican al insertar en la base.

## Verificar
```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://usuario:password@localhost:5432/bladys_test"
uv run pytest -q
uv run alembic upgrade head --sql
```

Los tests requieren PostgreSQL y permisos para crear un schema temporal.
Sin TEST_DATABASE_URL se omiten; eso no verifica integridad en PostgreSQL.
El downgrade elimina las tres tablas y sus datos.
