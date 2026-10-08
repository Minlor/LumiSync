"""Errors safe to display without exposing vendor responses or credentials."""


class AccountError(RuntimeError):
    def __init__(self, message: str, code: str = "account_error") -> None:
        super().__init__(message)
        self.code = code
