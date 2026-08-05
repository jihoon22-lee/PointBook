from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app.auth import current_user, require_login
from app.template_utils import render

router = APIRouter(dependencies=[Depends(require_login)], tags=["home"])


@router.get("/", response_class=Response)
def home(request: Request) -> Response:
    return render(request, "home.html", {"username": current_user(request)})
