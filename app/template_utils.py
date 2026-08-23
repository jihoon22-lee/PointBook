from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from app._version import __version__
from app.services.identifiers import format_point_no

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

templates.env.globals["version"] = __version__


def number_format(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


def _relative_luminance(hex_color: str) -> float:
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_color(background: str) -> str:
    """팀 배경색과 더 높은 WCAG 대비를 내는 흰색/검정을 반환한다."""
    if len(background) != 7 or not background.startswith("#"):
        return "#ffffff"
    try:
        background_luminance = _relative_luminance(background)
    except ValueError:
        return "#ffffff"
    dark_luminance = _relative_luminance("#000000")
    contrast_with_dark = (background_luminance + 0.05) / (dark_luminance + 0.05)
    contrast_with_white = 1.05 / (background_luminance + 0.05)
    return "#000000" if contrast_with_dark >= contrast_with_white else "#ffffff"


templates.env.filters["number_format"] = number_format
templates.env.filters["point_no"] = format_point_no
templates.env.filters["contrast_color"] = contrast_color


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(request, name, context or {}, status_code=status_code)
