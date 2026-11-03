-- Rows of every table of the schema public, one line «table|rows», for scripts/restore-check.sh.
select table_name || '|' || (xpath('/row/c/text()', query_to_xml(
  format('select count(*) c from %I.%I', table_schema, table_name), false, true, '')))[1]::text
from information_schema.tables
where table_schema = 'public' and table_type = 'BASE TABLE'
order by 1;
