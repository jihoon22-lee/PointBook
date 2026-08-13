from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from app._version import __version__

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

templates.env.globals["version"] = __version__


def number_format(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


templates.env.filters["number_format"] = number_format


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(request, name, context or {}, status_code=status_code)
