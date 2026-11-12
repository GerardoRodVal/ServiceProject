"""La IA que contesta WhatsApp. El proveedor sale de variables de entorno (ver config.py):

- anthropic: la API de Claude (producción). La clave va en ANTHROPIC_API_KEY; en Azure, desde Key Vault.
- ollama:    un modelo local (desarrollo en Docker: apunta al Ollama de la PC).
- vacío:     sin IA. Los mensajes se guardan y los contesta un asesor desde el gestor.

responder() nunca lanza: si el proveedor falla, rechaza o no contesta, devuelve None y la conversación queda para un
asesor. Así un problema de la IA no rompe el webhook.
"""
import json
import logging
import os
import urllib.error
import urllib.request

import anthropic

log = logging.getLogger(__name__)

MODELO_ANTHROPIC = "claude-opus-5-5"
MODELO_OLLAMA = "llama3.1:8b"
# Modelos que aceptan el respaldo automático de Anthropic: si el modelo declina por sus filtros de seguridad, la API
# reintenta la misma petición con el modelo que recomienda para ese caso, dentro de la misma llamada.
CON_RESPALDO = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
MAX_CARACTERES = 4000            # WhatsApp admite hasta 4096 por mensaje
_cliente = None


def turnos(mensajes):
    """[(rol, texto)] -> mensajes de chat: empiezan con el cliente y alternan; los seguidos del mismo lado se juntan."""
    salida = []
    for rol, texto in mensajes:
        if not texto:
            continue
        if salida and salida[-1]["role"] == rol:
            salida[-1]["content"] += "\n" + texto
        else:
            salida.append({"role": rol, "content": texto})
    while salida and salida[0]["role"] != "user":
        salida.pop(0)
    return salida


def modelo(cfg):
    if cfg.ia_proveedor == "anthropic":
        return cfg.ia_modelo or MODELO_ANTHROPIC
    if cfg.ia_proveedor == "ollama":
        return cfg.ia_modelo or MODELO_OLLAMA
    return ""


def estado(cfg):
    """{proveedor, modelo, listo, detalle}: si la IA puede contestar ahora. No gasta tokens."""
    base = {"proveedor": cfg.ia_proveedor, "modelo": modelo(cfg)}
    if cfg.ia_proveedor == "anthropic":
        con_clave = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return {**base, "listo": con_clave,
                "detalle": "Claude configurado." if con_clave else "Falta ANTHROPIC_API_KEY en el servidor."}
    if cfg.ia_proveedor == "ollama":
        try:
            with urllib.request.urlopen(cfg.ollama_url.rstrip("/") + "/api/tags", timeout=3) as r:
                nombres = [m.get("name", "") for m in json.loads(r.read() or b"{}").get("models", [])]
        except (OSError, ValueError):
            return {**base, "listo": False, "detalle": "El servidor no alcanza a Ollama: enciéndelo en esta PC."}
        tiene = base["modelo"] in nombres or f"{base['modelo']}:latest" in nombres
        return {**base, "listo": tiene, "detalle": f"Ollama con {base['modelo']}." if tiene
                else f"Ollama no tiene el modelo {base['modelo']}: descárgalo con «ollama pull {base['modelo']}»."}
    return {**base, "listo": False, "detalle": "El servidor no tiene IA configurada (IA_PROVEEDOR)."}


def responder(cfg, sistema, mensajes):
    """mensajes: [(«user» | «assistant», texto)] con el último del cliente. Devuelve el texto de la respuesta o None."""
    chat = turnos(mensajes)
    if not chat or chat[-1]["role"] != "user":
        return None
    if cfg.ia_proveedor == "anthropic":
        texto = _anthropic(cfg, sistema, chat)
    elif cfg.ia_proveedor == "ollama":
        texto = _ollama(cfg, sistema, chat)
    else:
        return None
    return texto[:MAX_CARACTERES] if texto else None


def _anthropic(cfg, sistema, chat):
    global _cliente
    if _cliente is None:
        _cliente = anthropic.Anthropic(timeout=60.0, max_retries=2)
    modelo = cfg.ia_modelo or MODELO_ANTHROPIC
    respaldo = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"} if modelo in CON_RESPALDO else {}
    try:
        r = _cliente.beta.messages.create(
            model=modelo,
            max_tokens=4000,
            system=sistema,
            messages=chat,
            # Respuestas cortas de chat: con esfuerzo bajo contesta rápido y gasta menos.
            output_config={"effort": "low"},
            **respaldo,
        )
    except anthropic.RateLimitError:
        log.warning("Claude: límite de peticiones alcanzado; la conversación queda para un asesor.")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude respondió %s (%s); la conversación queda para un asesor.", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("Sin conexión con Claude; la conversación queda para un asesor.")
        return None
    if r.stop_reason == "refusal":
        log.info("Claude declinó contestar (%s); la conversación queda para un asesor.",
                 getattr(r.stop_details, "category", None))
        return None
    return "".join(b.text for b in r.content if b.type == "text").strip() or None


def _ollama(cfg, sistema, chat):
    cuerpo = json.dumps({"model": cfg.ia_modelo or MODELO_OLLAMA, "stream": False,
                         "messages": [{"role": "system", "content": sistema}] + chat}).encode("utf-8")
    req = urllib.request.Request(cfg.ollama_url.rstrip("/") + "/api/chat", data=cuerpo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            datos = json.loads(r.read() or b"{}")
    except (OSError, ValueError) as e:
        log.warning("Ollama no respondió (%s); la conversación queda para un asesor.", e)
        return None
    return ((datos.get("message") or {}).get("content") or "").strip() or None
