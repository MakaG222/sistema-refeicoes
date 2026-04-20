"""Geração de QR codes — dependência `qrcode` opcional.

Estratégia: se `qrcode` (pure-Python) estiver instalado, geramos SVG sem
precisar de Pillow. Caso contrário, devolvemos um SVG de fallback com o
texto em monoespaço (sempre utilizável por USB-reader que lê caracteres).

Uso típico:

    svg_bytes = qr_svg_bytes(f"NII:{aluno['NII']}")

O payload codificado é prefixado com `NII:` para o scanner saber que é um
identificador de utilizador (permite distinguir de outros QRs no futuro).
"""

from __future__ import annotations

import html
import io
import logging

log = logging.getLogger(__name__)

QR_PAYLOAD_PREFIX = "NII:"


def _fallback_svg(text: str) -> bytes:
    """SVG minimalista que renderiza apenas o texto — usado quando `qrcode`
    não está disponível. Não é scanneável por câmara mas serve como
    placeholder legível.
    """
    safe = html.escape(text)
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='220' height='220' "
        "viewBox='0 0 220 220'>"
        "<rect width='220' height='220' fill='#fff' stroke='#000'/>"
        "<text x='110' y='100' text-anchor='middle' "
        "font-family='monospace' font-size='14' fill='#000'>QR indisponível</text>"
        f"<text x='110' y='130' text-anchor='middle' "
        f"font-family='monospace' font-size='16' fill='#000'>{safe}</text>"
        "</svg>"
    )
    return svg.encode("utf-8")


def qr_svg_bytes(data: str) -> bytes:
    """Gera um SVG do QR para `data`. Retorna bytes (image/svg+xml).

    Se a lib `qrcode` não estiver instalada, devolve um SVG de fallback
    que mostra o texto em plano — cliente ainda pode copiar o NII à mão.
    """
    try:
        import qrcode
        import qrcode.image.svg as _svg
    except ImportError:
        log.info("qrcode ausente — a usar fallback SVG textual.")
        return _fallback_svg(data)

    buf = io.BytesIO()
    img = qrcode.make(
        data,
        image_factory=_svg.SvgPathImage,
        border=2,
        box_size=10,
    )
    img.save(buf)
    return buf.getvalue()


def build_payload(nii: str) -> str:
    """Constrói o payload canónico para um aluno."""
    return f"{QR_PAYLOAD_PREFIX}{nii}"


def parse_payload(raw: str) -> str | None:
    """Extrai o NII de um payload scaneado. Aceita formatos:
    - `NII:123`    → '123'
    - `123`        → '123'   (fallback: o reader só enviou o NII)
    - qualquer outra coisa → None
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    if raw.startswith(QR_PAYLOAD_PREFIX):
        return raw[len(QR_PAYLOAD_PREFIX) :].strip() or None
    # Se não tem prefixo mas parece um NII simples, aceita
    if raw.replace("-", "").replace("_", "").isalnum():
        return raw
    return None


__all__ = ["qr_svg_bytes", "build_payload", "parse_payload", "QR_PAYLOAD_PREFIX"]
