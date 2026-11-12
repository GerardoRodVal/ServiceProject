"""Limpieza de lo que llega del celular. Mismo criterio que limpiar_visita del gestor de Windows:
solo se aceptan los campos conocidos, con límites de tamaño; nunca se guarda el contenido tal cual."""
import re
from datetime import datetime, timezone

CAMPOS_TEXTO = {"nombre": 80, "tel": 30, "asesor": 60, "notas": 1000, "propiedad": 60, "giro": 40,
                "credito": 20, "plazo": 4, "decide": 10}
ESTADOS = ("nuevo", "contactado", "negociando", "cerrado", "perdido")
CATEGORIAS = ("alta", "media", "baja")
MAX_BYTES_VISITA = 64 * 1024


def limpiar_enlace(d):
    """Campos internos que solo escribe el gestor (nunca el celular): asesor que capturó, origen y enlace a Kommo."""
    salida = {}
    if d.get("asesor_id") is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(d["asesor_id"])):
            raise ValueError("asesor_id inválido.")
        salida["asesor_id"] = str(d["asesor_id"])
    if d.get("origen") is not None:
        if not re.fullmatch(r"[a-z0-9_-]{1,20}", str(d["origen"])):
            raise ValueError("origen inválido.")
        salida["origen"] = str(d["origen"])
    if d.get("kommo_id") is not None:
        if isinstance(d["kommo_id"], bool) or not re.fullmatch(r"\d{1,20}", str(d["kommo_id"])):
            raise ValueError("kommo_id inválido.")
        salida["kommo_id"] = int(d["kommo_id"])
    if d.get("kommo_url") is not None:
        if not re.fullmatch(r"https://[a-z0-9-]+\.kommo\.com/[^\s]{0,200}", str(d["kommo_url"])):
            raise ValueError("kommo_url inválida.")
        salida["kommo_url"] = str(d["kommo_url"])
    return salida


def limpiar_visita(d, interno=False):
    """interno=True: además de lo que manda el celular, acepta los campos que escribe el gestor (ver limpiar_enlace)."""
    if not isinstance(d, dict):
        raise ValueError("Formato inválido.")
    id_ = str(d.get("id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{6,64}", id_):
        raise ValueError("Identificador inválido.")
    try:
        creado = datetime.fromisoformat(str(d.get("creado", "")).replace("Z", "+00:00"))
        if creado.tzinfo is None:
            creado = creado.replace(tzinfo=timezone.utc)
    except ValueError:
        creado = datetime.now(timezone.utc)
    salida = {"id": id_, "creado": creado.astimezone(timezone.utc).isoformat()}
    for campo, largo in CAMPOS_TEXTO.items():
        salida[campo] = str(d.get(campo, ""))[:largo]
    for campo, tope in (("presupuesto", 10**10), ("enganche", 100), ("score", 100)):
        try:
            salida[campo] = max(0, min(int(float(d.get(campo, 0) or 0)), tope))
        except (TypeError, ValueError):
            raise ValueError(f"Valor inválido en {campo}.")
    salida["cat"] = d.get("cat") if d.get("cat") in CATEGORIAS else "baja"
    razones = d.get("razones") if isinstance(d.get("razones"), list) else []
    salida["razones"] = [{"txt": str(r.get("txt", ""))[:80], "pts": int(r.get("pts", 0) or 0)}
                         for r in razones[:10] if isinstance(r, dict)]
    extras = d.get("extras") if isinstance(d.get("extras"), dict) else {}
    salida["extras"] = {str(k): str(v)[:300] for k, v in list(extras.items())[:20]
                        if re.fullmatch(r"[a-z0-9_-]{1,40}", str(k))}
    provisionales = d.get("provisionales") if isinstance(d.get("provisionales"), list) else []
    salida["provisionales"] = [str(x) for x in provisionales[:30] if re.fullmatch(r"[a-z0-9_-]{1,40}", str(x))]
    if interno:
        salida.update(limpiar_enlace(d))
    return salida
