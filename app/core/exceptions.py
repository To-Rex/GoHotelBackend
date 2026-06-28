from __future__ import annotations


class AppException(Exception):
    def __init__(self, detail: str = "An error occurred", error_code: str = "APP_ERROR") -> None:
        self.status_code: int = 500
        self.detail = detail
        self.error_code = error_code
        super().__init__(self.detail)


class NotFoundException(AppException):
    def __init__(self, detail: str = "Resource not found", error_code: str = "NOT_FOUND") -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 404


class ForbiddenException(AppException):
    def __init__(self, detail: str = "Access forbidden", error_code: str = "FORBIDDEN") -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 403


class UnauthorizedException(AppException):
    def __init__(self, detail: str = "Unauthorized", error_code: str = "UNAUTHORIZED") -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 401


class ConflictException(AppException):
    def __init__(self, detail: str = "Resource conflict", error_code: str = "CONFLICT") -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 409


class ValidationException(AppException):
    def __init__(self, detail: str = "Validation error", error_code: str = "VALIDATION_ERROR") -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 422


class BadRequestException(AppException):
    def __init__(self, detail: str = "Bad request", error_code: str = "BAD_REQUEST") -> None:
        super().__init__(detail=detail, error_code=error_code)
        self.status_code = 400
