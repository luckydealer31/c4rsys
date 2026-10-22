"""Single error format of the API (A08, docs/stage1/STAGE1_COMMANDS_API.md).

A business-rule refusal is distinct from a technical failure; responses never reveal data of
objects the caller cannot access.
"""

from typing import Any


class ApiError(Exception):
    """Error returned to the client with a stable machine code and a translation key."""

    def __init__(
        self,
        code: str,
        *,
        status: int,
        params: dict[str, Any] | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params or {}
        self.field = field

    @property
    def message_key(self) -> str:
        return f"errors.{self.code.lower()}"

    def payload(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message_key": self.message_key,
                "params": self.params,
                "field": self.field,
                "request_id": request_id,
            }
        }


def validation_error(field: str, code: str = "VALIDATION_ERROR", **params: Any) -> ApiError:
    return ApiError(code, status=400, field=field, params=params)


def permission_denied(permission: str | None = None) -> ApiError:
    return ApiError("PERMISSION_DENIED", status=403, params={"permission": permission})


def not_found() -> ApiError:
    """Same answer for a missing object and an object of another company (ТЗ § 2.8.1)."""
    return ApiError("NOT_FOUND", status=404)


def version_conflict() -> ApiError:
    return ApiError("VERSION_CONFLICT", status=409)


def business_rule(code: str, *, field: str | None = None, **params: Any) -> ApiError:
    return ApiError(code, status=422, field=field, params=params)


def idempotency_key_reused() -> ApiError:
    return ApiError("IDEMPOTENCY_KEY_REUSED", status=409)


def operation_not_posted() -> ApiError:
    """Technical failure inside a command, including a failed journal write (decision 22.10)."""
    return ApiError("OPERATION_NOT_POSTED", status=500)


def internal_error() -> ApiError:
    return ApiError("INTERNAL_ERROR", status=500)
