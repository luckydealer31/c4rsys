"""Sequential numbers inside a company (ТЗ § 12.1.1, § 12.2.5)."""

from uuid import UUID

from django.db import connection

from c4rsys.platform.models import CompanyCounter


def next_number(company_id: UUID, kind: str) -> int:
    """Next number of `kind` for the company. Must run inside the command's transaction: the row
    stays locked until commit, so concurrent commands get consecutive numbers."""
    CompanyCounter.objects.get_or_create(company_id=company_id, kind=kind)
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE company_counter SET last_value = last_value + 1 "
            "WHERE company_id = %s AND kind = %s RETURNING last_value",
            [company_id, kind],
        )
        row = cursor.fetchone()
    return int(row[0])
