from fastapi import HTTPException, Request

SESSION_KEY = "admin_username"


def login_user(request: Request, username: str) -> None:
    request.session[SESSION_KEY] = username


def logout_user(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def current_user(request: Request) -> str | None:
    return request.session.get(SESSION_KEY)


def require_login(request: Request) -> None:
    if current_user(request) is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
