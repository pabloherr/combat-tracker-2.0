"""
Correo saliente: plantillas y envío por SMTP.

Dos modos, y el que manda es si hay credenciales (`MAIL_PASSWORD`):

  * **smtp**   → se envía de verdad, en un hilo aparte para no frenar la
                 petición (un SMTP lento tarda segundos).
  * **outbox** → sin credenciales no se pierde nada: el mensaje se escribe en
                 `outbox/` como `.eml` (se abre con doble clic) y queda en el
                 registro. Sirve para una LAN sin internet y para los tests.

Todo lo que sale queda en la tabla `mail_log`, que es lo que muestra el panel
de administración.
"""

import re
import smtplib
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from . import settings
from .database import db

# Últimos mensajes generados, en memoria. Los tests miran acá y el panel lo usa
# para mostrar el contenido del último envío sin guardar cuerpos en la base.
SENT: list[dict] = []
_SENT_MAX = 50
_lock = threading.Lock()


def _remember(msg: dict):
    with _lock:
        SENT.append(msg)
        del SENT[:-_SENT_MAX]


def _sin_codigo(subject: str) -> str:
    """Tapa el código de seis dígitos antes de guardarlo en `mail_log`.

    El asunto lo lleva a propósito (se ve en la notificación del celular sin
    abrir el mensaje), pero el registro lo mira el administrador desde el
    panel: ahí el código no tiene por qué estar."""
    return re.sub(r"\d{6}", "••••••", subject or "")


def _log(to_addr: str, subject: str, kind: str, ok: bool, error: str, mode: str):
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO mail_log (to_addr, subject, kind, ok, error, mode) VALUES (?,?,?,?,?,?)",
                (to_addr, _sin_codigo(subject), kind, 1 if ok else 0, error[:400], mode),
            )
    except Exception:
        # El correo no puede tumbar una petición por un problema al registrar.
        pass


def _build(to_addr: str, subject: str, text: str, html: str) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((settings.mail_from_name(), settings.mail_user()))
    m["To"] = to_addr
    m["Subject"] = subject
    m["Date"] = formatdate(localtime=True)
    m.set_content(text)
    m.add_alternative(html, subtype="html")
    return m


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-")[:40] or "mail"


def _write_outbox(msg: EmailMessage, kind: str):
    if settings.env("MAIL_OUTBOX", "1").lower() in ("0", "false", "no"):
        return
    box = settings.OUTBOX
    box.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    (box / f"{stamp}-{_slug(kind or 'mail')}.eml").write_bytes(bytes(msg))


def _smtp_send(msg: EmailMessage):
    host, port = settings.mail_host(), settings.mail_port()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context()) as s:
            s.login(settings.mail_user(), settings.mail_password())
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ssl.create_default_context())
            s.login(settings.mail_user(), settings.mail_password())
            s.send_message(msg)


def send(to_addr: str, subject: str, text: str, html: str, kind: str = "") -> bool:
    """Manda (o encola) un mensaje. Devuelve False solo si falla en el acto.

    En modo smtp el envío ocurre en un hilo: acá devuelve True enseguida y el
    resultado real queda en `mail_log` (y en el panel)."""
    to_addr = (to_addr or "").strip()
    if not to_addr:
        return False
    msg = _build(to_addr, subject, text, html)
    _remember({"to": to_addr, "subject": subject, "text": text, "kind": kind,
               "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    if not settings.mail_enabled():
        try:
            _write_outbox(msg, kind)
            _log(to_addr, subject, kind, True, "", "outbox")
            return True
        except Exception as e:
            _log(to_addr, subject, kind, False, str(e), "outbox")
            return False

    def worker():
        try:
            _smtp_send(msg)
            _log(to_addr, subject, kind, True, "", "smtp")
        except Exception as e:
            _log(to_addr, subject, kind, False, f"{type(e).__name__}: {e}", "smtp")

    threading.Thread(target=worker, daemon=True, name="mailer").start()
    return True


def send_now(to_addr: str, subject: str, text: str, html: str, kind: str = "") -> tuple[bool, str]:
    """Igual que `send` pero sin hilo y contando qué pasó. Lo usa el botón de
    prueba del panel, que necesita ver el error del SMTP en pantalla."""
    to_addr = (to_addr or "").strip()
    if not to_addr:
        return False, "Falta el destinatario"
    msg = _build(to_addr, subject, text, html)
    _remember({"to": to_addr, "subject": subject, "text": text, "kind": kind,
               "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if not settings.mail_enabled():
        try:
            _write_outbox(msg, kind)
            _log(to_addr, subject, kind, True, "", "outbox")
            return True, "Sin credenciales SMTP: se guardó en outbox/"
        except Exception as e:
            _log(to_addr, subject, kind, False, str(e), "outbox")
            return False, str(e)
    try:
        _smtp_send(msg)
        _log(to_addr, subject, kind, True, "", "smtp")
        return True, "Enviado"
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        _log(to_addr, subject, kind, False, err, "smtp")
        return False, err


def status() -> dict:
    """Cómo está configurado el correo (para el panel)."""
    return {
        "enabled": settings.mail_enabled(),
        "mode": "smtp" if settings.mail_enabled() else "outbox",
        "host": settings.mail_host(),
        "port": settings.mail_port(),
        "user": settings.mail_user(),
        "from_name": settings.mail_from_name(),
        "base_url": settings.base_url(),
        "outbox": str(settings.OUTBOX),
    }


# ── Plantillas ─────────────────────────────────────────────
# HTML con estilos en línea (los clientes de correo ignoran las hojas aparte)
# y una versión de texto plano, que es la que ve quien tenga el HTML apagado.

_WRAP = """<!DOCTYPE html><html><body style="margin:0;padding:24px;background:#211f1c;
 font-family:Georgia,'Times New Roman',serif;color:#efece4">
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
   style="max-width:520px;background:#2c2924;border:1px solid rgba(224,189,124,.28);border-radius:14px">
   <tr><td style="padding:26px 28px 8px">
     <div style="font-family:Georgia,serif;font-size:13px;letter-spacing:.18em;
      text-transform:uppercase;color:#e0bd7c">Cosmere Combat Tracker</div>
     <div style="height:1px;background:rgba(224,189,124,.22);margin:14px 0 18px"></div>
     {body}
   </td></tr>
   <tr><td style="padding:6px 28px 24px">
     <div style="height:1px;background:rgba(224,189,124,.14);margin:18px 0 12px"></div>
     <div style="font-size:12px;color:#948d7b;font-style:italic">{foot}</div>
   </td></tr>
  </table>
 </td></tr></table>
</body></html>"""

_H = ('font-family:Georgia,serif;font-size:19px;color:#f4d9a0;'
      'letter-spacing:.03em;margin:0 0 10px')
_P = 'font-size:15px;line-height:1.6;color:#efece4;margin:0 0 14px'
_CODE = ('display:inline-block;font-family:Consolas,monospace;font-size:30px;'
         'letter-spacing:.34em;color:#f4d9a0;background:#353129;'
         'border:1px solid rgba(224,189,124,.28);border-radius:10px;'
         'padding:14px 20px 14px 26px')
_BTN = ('display:inline-block;background:#e0bd7c;color:#1c1915;text-decoration:none;'
        'font-family:Georgia,serif;font-size:14px;letter-spacing:.08em;'
        'text-transform:uppercase;padding:12px 22px;border-radius:10px')


def _page(body: str, foot: str) -> str:
    return _WRAP.format(body=body, foot=foot)


def reset_email(username: str, code: str, link: str, minutes: int) -> tuple[str, str, str]:
    """Pediste recuperar la contraseña: código + enlace."""
    subject = f"Recuperar tu contraseña ({code})"
    html = _page(
        f'<div style="{_H}">Recuperar tu contraseña</div>'
        f'<p style="{_P}">Hola <b style="color:#f4d9a0">{username}</b>: alguien pidió '
        f'restablecer la contraseña de tu cuenta. Si fuiste vos, usá este código:</p>'
        f'<p style="text-align:center;margin:18px 0"><span style="{_CODE}">{code}</span></p>'
        f'<p style="{_P}">O entrá directo desde acá:</p>'
        f'<p style="text-align:center;margin:16px 0 6px"><a href="{link}" style="{_BTN}">Elegir contraseña nueva</a></p>'
        f'<p style="font-size:12px;color:#948d7b;word-break:break-all;margin:10px 0 0">{link}</p>',
        f"El código vence en {minutes} minutos y sirve una sola vez. "
        f"Si no pediste esto, ignorá el mensaje: tu contraseña sigue igual.")
    text = (f"Hola {username}:\n\n"
            f"Pediste restablecer tu contraseña en Cosmere Combat Tracker.\n\n"
            f"Código: {code}\n"
            f"Enlace: {link}\n\n"
            f"Vence en {minutes} minutos y sirve una sola vez.\n"
            f"Si no fuiste vos, ignorá este mensaje: tu contraseña sigue igual.\n")
    return subject, text, html


def change_email(username: str, code: str, link: str, minutes: int) -> tuple[str, str, str]:
    """Cambio de contraseña pedido desde la cuenta: hay que confirmarlo."""
    subject = f"Confirmá el cambio de contraseña ({code})"
    html = _page(
        f'<div style="{_H}">Confirmá el cambio</div>'
        f'<p style="{_P}">Hola <b style="color:#f4d9a0">{username}</b>: pediste cambiar '
        f'la contraseña de tu cuenta. El cambio todavía no se aplicó. Para confirmarlo, '
        f'usá este código:</p>'
        f'<p style="text-align:center;margin:18px 0"><span style="{_CODE}">{code}</span></p>'
        f'<p style="text-align:center;margin:16px 0 6px"><a href="{link}" style="{_BTN}">Confirmar el cambio</a></p>'
        f'<p style="font-size:12px;color:#948d7b;word-break:break-all;margin:10px 0 0">{link}</p>',
        f"El código vence en {minutes} minutos. Si no pediste el cambio, no confirmes: "
        f"tu contraseña actual sigue funcionando.")
    text = (f"Hola {username}:\n\n"
            f"Pediste cambiar tu contraseña. El cambio NO se aplicó todavía.\n\n"
            f"Código de confirmación: {code}\n"
            f"Enlace: {link}\n\n"
            f"Vence en {minutes} minutos.\n"
            f"Si no fuiste vos, no confirmes nada: tu contraseña sigue igual.\n")
    return subject, text, html


def changed_notice(username: str, cuando: str, motivo: str) -> tuple[str, str, str]:
    """Aviso posterior: la contraseña ya cambió."""
    subject = "Tu contraseña cambió"
    html = _page(
        f'<div style="{_H}">Tu contraseña cambió</div>'
        f'<p style="{_P}">Hola <b style="color:#f4d9a0">{username}</b>: la contraseña de '
        f'tu cuenta se cambió {motivo} el {cuando}. Se cerraron todas las sesiones abiertas.</p>'
        f'<p style="{_P}">Si fuiste vos, listo, no hay nada que hacer.</p>',
        "Si no fuiste vos, entrá con la opción de recuperar contraseña para volver a tomar "
        "el control de la cuenta, o avisale al administrador.")
    text = (f"Hola {username}:\n\n"
            f"La contraseña de tu cuenta se cambió {motivo} el {cuando}.\n"
            f"Se cerraron todas las sesiones abiertas.\n\n"
            f"Si no fuiste vos, usá 'recuperar contraseña' para retomar la cuenta.\n")
    return subject, text, html


def account_email_changed(username: str, viejo: str, nuevo: str) -> tuple[str, str, str]:
    subject = "El email de tu cuenta cambió"
    html = _page(
        f'<div style="{_H}">El email de tu cuenta cambió</div>'
        f'<p style="{_P}">Hola <b style="color:#f4d9a0">{username}</b>: el email de tu cuenta '
        f'pasó de <b>{viejo or "(vacío)"}</b> a <b style="color:#f4d9a0">{nuevo}</b>.</p>'
        f'<p style="{_P}">Desde ahora, la recuperación de contraseña llega a la dirección nueva.</p>',
        "Si no fuiste vos, avisale al administrador cuanto antes.")
    text = (f"Hola {username}:\n\n"
            f"El email de tu cuenta pasó de {viejo or '(vacío)'} a {nuevo}.\n"
            f"La recuperación de contraseña ahora llega a la dirección nueva.\n")
    return subject, text, html


def admin_reset_notice(username: str, code: str, link: str, minutes: int) -> tuple[str, str, str]:
    """El administrador disparó una recuperación para esta cuenta."""
    subject = f"Restablecé tu contraseña ({code})"
    html = _page(
        f'<div style="{_H}">Restablecé tu contraseña</div>'
        f'<p style="{_P}">Hola <b style="color:#f4d9a0">{username}</b>: el administrador de '
        f'Cosmere Combat Tracker inició un restablecimiento de contraseña para tu cuenta.</p>'
        f'<p style="text-align:center;margin:18px 0"><span style="{_CODE}">{code}</span></p>'
        f'<p style="text-align:center;margin:16px 0 6px"><a href="{link}" style="{_BTN}">Elegir contraseña nueva</a></p>'
        f'<p style="font-size:12px;color:#948d7b;word-break:break-all;margin:10px 0 0">{link}</p>',
        f"El código vence en {minutes} minutos y sirve una sola vez.")
    text = (f"Hola {username}:\n\n"
            f"El administrador inició un restablecimiento de contraseña para tu cuenta.\n\n"
            f"Código: {code}\nEnlace: {link}\n\nVence en {minutes} minutos.\n")
    return subject, text, html


def test_email(destino: str) -> tuple[str, str, str]:
    subject = "Prueba de correo — Cosmere Combat Tracker"
    html = _page(
        f'<div style="{_H}">El correo anda</div>'
        f'<p style="{_P}">Este es un mensaje de prueba enviado desde el panel de '
        f'administración a <b style="color:#f4d9a0">{destino}</b>.</p>'
        f'<p style="{_P}">Si lo estás leyendo, la recuperación de contraseña también va a llegar.</p>',
        "Enviado a mano desde el panel. No hace falta responder.")
    text = ("Mensaje de prueba de Cosmere Combat Tracker.\n"
            "Si lo estás leyendo, el correo saliente funciona.\n")
    return subject, text, html
