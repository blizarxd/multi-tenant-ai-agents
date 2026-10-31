"""
Envío de emails con adjunto PDF.

Usa Gmail SMTP con App Password (no la contraseña normal de Google).

Para obtener un App Password:
  1. Google Account → Seguridad → Verificación en 2 pasos (debe estar activa)
  2. Google Account → Seguridad → Contraseñas de aplicaciones
  3. Selecciona "Correo" + "Windows" → Genera → copia los 16 caracteres
  4. Guarda en .env: GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
     o en el config.json del tenant: "gmail_app_password": "..."
"""

import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders
from datetime import datetime

logger = logging.getLogger(__name__)


def send_pdf_report(to_email: str, subject: str, pdf_bytes: bytes,
                    from_email: str, app_password: str) -> bool:
    """
    Envía un PDF como adjunto por Gmail SMTP.
    Retorna True si se envió correctamente.
    """
    if not from_email or not app_password:
        raise ValueError(
            "Faltan credenciales de Gmail. "
            "Configura gmail_sender y gmail_app_password en el config.json del tenant "
            "o GMAIL_APP_PASSWORD en el .env"
        )

    msg = MIMEMultipart()
    msg["From"]    = from_email
    msg["To"]      = to_email
    msg["Subject"] = subject

    # Cuerpo del correo
    body = (
        f"Estimado doctor:\n\n"
        f"Adjunto el reporte diario de su consultorio generado el "
        f"{datetime.now().strftime('%d/%m/%Y a las %H:%M')}.\n\n"
        f"El reporte incluye:\n"
        f"  - Agenda del día (citas agendadas)\n"
        f"  - Alertas de urgencia\n"
        f"  - Cancelaciones\n"
        f"  - Resumen de pacientes\n\n"
        f"Sistema de Agentes IA — Táchira"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Adjunto PDF
    attachment = MIMEBase("application", "octet-stream")
    attachment.set_payload(pdf_bytes)
    encoders.encode_base64(attachment)
    filename = f"reporte_{datetime.now().strftime('%Y%m%d')}.pdf"
    attachment.add_header("Content-Disposition", f"attachment; filename={filename}")
    msg.attach(attachment)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_email, app_password)
            server.sendmail(from_email, to_email, msg.as_string())
        logger.info(f"Reporte enviado a {to_email}")
        return True
    except Exception as e:
        logger.error(f"Error enviando email: {e}")
        raise


def send_appointment_confirmation(to_email: str, patient_name: str,
                                   appointment_data: dict,
                                   from_email: str, app_password: str) -> bool:
    """
    Envía confirmación de cita al paciente (si proporcionó correo).
    """
    if not to_email or not from_email or not app_password:
        return False

    subject = f"Confirmación de cita — {appointment_data.get('fecha', '')}"
    body = (
        f"Hola {patient_name},\n\n"
        f"Tu cita ha sido confirmada:\n\n"
        f"  Fecha:    {appointment_data.get('fecha', '-')}\n"
        f"  Servicio: {appointment_data.get('servicio', '-')}\n"
        f"  Precio:   ${appointment_data.get('precio_usd', '-')} USD\n\n"
        f"Por favor llega 5 minutos antes. Ante cualquier cambio avísanos por Telegram.\n\n"
        f"¡Hasta pronto!"
    )

    msg = MIMEMultipart()
    msg["From"]    = from_email
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_email, app_password)
            server.sendmail(from_email, to_email, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Error enviando confirmación: {e}")
        return False
