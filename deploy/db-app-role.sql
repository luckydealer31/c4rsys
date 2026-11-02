-- The application connects as c4rsys_app, not as the superuser c4rsys of the image (audit item 38).
-- c4rsys_app owns the database, the schema and its objects, so migrations still run, but it cannot
-- run programs on the database server, read its files, reach other databases or change roles.
-- Run by the db-role service before the backend starts; safe to run again. The password comes from
-- C4R_DB_APP_PASSWORD in the environment and never appears on a command line.
\set ON_ERROR_STOP on
\getenv app_password C4R_DB_APP_PASSWORD
SELECT :'app_password' <> '' AS has_password \gset
\if :has_password
\else
  \echo 'C4R_DB_APP_PASSWORD is empty'
  \quit 1
\endif

SELECT NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'c4rsys_app') AS create_role \gset
\if :create_role
  CREATE ROLE c4rsys_app LOGIN;
\endif
ALTER ROLE c4rsys_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
  PASSWORD :'app_password';

ALTER DATABASE c4rsys OWNER TO c4rsys_app;
ALTER SCHEMA public OWNER TO c4rsys_app;

DO $$
DECLARE
  item record;
BEGIN
  -- Tables first: their indexes and own sequences (identity, serial) follow them.
  FOR item IN
    SELECT c.oid::regclass AS name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      AND pg_get_userbyid(c.relowner) <> 'c4rsys_app'
      AND NOT EXISTS (
        SELECT FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('a', 'i')
      )
    ORDER BY c.relkind <> 'r' AND c.relkind <> 'p'
  LOOP
    IF item.relkind = 'S' THEN
      EXECUTE format('ALTER SEQUENCE %s OWNER TO c4rsys_app', item.name);
    ELSIF item.relkind = 'v' THEN
      EXECUTE format('ALTER VIEW %s OWNER TO c4rsys_app', item.name);
    ELSIF item.relkind = 'm' THEN
      EXECUTE format('ALTER MATERIALIZED VIEW %s OWNER TO c4rsys_app', item.name);
    ELSIF item.relkind = 'f' THEN
      EXECUTE format('ALTER FOREIGN TABLE %s OWNER TO c4rsys_app', item.name);
    ELSE
      EXECUTE format('ALTER TABLE %s OWNER TO c4rsys_app', item.name);
    END IF;
  END LOOP;

  -- Trigger functions of the append-only journals; functions of extensions stay with them.
  FOR item IN
    SELECT p.oid::regprocedure AS name
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
      AND pg_get_userbyid(p.proowner) <> 'c4rsys_app'
      AND NOT EXISTS (
        SELECT FROM pg_depend d
        WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e'
      )
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO c4rsys_app', item.name);
  END LOOP;
END
$$;
