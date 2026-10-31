"""
Subida de archivos a Google Drive vía gws CLI (ya autenticado).
Cuenta activa: your-account@example.com
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)

_GWS = shutil.which("gws") or shutil.which("gws.cmd") or "gws"


def _run_gws(*args) -> dict:
    cmd = [_GWS] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", shell=False)
    if result.returncode != 0:
        raise RuntimeError(f"gws error: {result.stderr.strip() or result.stdout.strip()}")
    text = result.stdout.strip()
    if not text:
        return {}
    return json.loads(text)


def _get_or_create_folder(folder_name: str) -> str | None:
    """Retorna el ID de una carpeta en Drive (la crea si no existe)."""
    try:
        params = {
            "q": f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            "fields": "files(id,name)"
        }
        result = _run_gws("drive", "files", "list", "--params", json.dumps(params))
        files = result.get("files", [])
        if files:
            return files[0]["id"]

        # Crear carpeta
        meta = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        created = _run_gws("drive", "files", "create", "--json", json.dumps(meta))
        folder_id = created.get("id")
        logger.info(f"Carpeta creada en Drive: {folder_name} ({folder_id})")
        return folder_id
    except Exception as e:
        logger.warning(f"No se pudo obtener/crear carpeta '{folder_name}': {e}")
        return None


def upload_file(file_bytes: bytes, filename: str,
                folder_name: str = "Reportes Médicos") -> str:
    """
    Sube bytes a Google Drive.
    Retorna la URL pública del archivo, o "" si falla.
    """
    tmp_path = None
    try:
        # Guardar en archivo temporal
        suffix = os.path.splitext(filename)[1] or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        folder_id = _get_or_create_folder(folder_name)

        # Metadata del archivo
        meta = {"name": filename}
        if folder_id:
            meta["parents"] = [folder_id]

        # Subir con gws. gws soporta --file para subidas multipart.
        result = _run_gws(
            "drive", "files", "create",
            "--file", tmp_path,
            "--json", json.dumps(meta)
        )

        file_id = result.get("id", "")
        if file_id:
            # Hacer el archivo visible para quien tenga el link
            try:
                _run_gws(
                    "drive", "permissions", "create",
                    "--params", json.dumps({"fileId": file_id}),
                    "--json", json.dumps({"role": "reader", "type": "anyone"})
                )
            except Exception:
                pass  # sin permiso público está bien igual

            url = f"https://drive.google.com/file/d/{file_id}/view"
            logger.info(f"Archivo subido a Drive: {filename} | {url}")
            return url

        logger.warning(f"gws no retornó ID al subir {filename}: {result}")
        return ""

    except Exception as e:
        logger.error(f"Error subiendo a Drive: {e}")
        return ""
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def save_report_locally(file_bytes: bytes, filename: str,
                        reports_dir: str = "reports") -> str:
    """Fallback: guarda el reporte en disco y retorna la ruta."""
    os.makedirs(reports_dir, exist_ok=True)
    path = os.path.join(reports_dir, filename)
    with open(path, "wb") as f:
        f.write(file_bytes)
    return os.path.abspath(path)
