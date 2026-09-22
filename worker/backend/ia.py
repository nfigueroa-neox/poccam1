"""Análisis de eventos con DeepSeek Vision (deepseek-v4-flash-vision-exp).

Cuando el detector confirma un cambio, se envía la imagen del evento a la
API de visión de DeepSeek y se guarda la respuesta con una ESTRUCTURA FIJA
de JSON (campos siempre presentes), listo para automatizar estadísticas.

Seguridad:
  La API Key NO se hardcodea. Se lee de, en orden:
    1) la variable de entorno DEEPSEEK_API_KEY
    2) el archivo `ia.key` en la raíz del proyecto (NO se versiona)
"""

import base64
import json
import logging

logger = logging.getLogger("backend")

# API compatible con OpenAI → endpoint de chat/completions
BASE_URL = "https://api.deepseek.com"
MODELO_DEFECTO = "deepseek-v4-flash-vision-exp"

# Prompt por defecto SOLO se usa si no existe ia_prompt.txt. Describe un
# caso genérico y pide un JSON libre (la estructura la define el usuario
# en su prompt; aquí es solo un ejemplo de arranque).
PROMPT_SISTEMA = (
    "Eres un analista de imágenes. Analiza la imagen que se te envía y "
    "responde en JSON indicando qué observas. Usa la estructura de "
    "campos que se defina en tu instrucción de sistema; si no se define "
    "ninguna, usa por defecto: {\"descripcion\": \"...\"}.\n"
    "No agregues texto fuera del JSON."
)


def _obtener_clave() -> str | None:
    """Devuelve la API key del entorno o del archivo ia.key (si existe)."""
    import os
    from pathlib import Path

    clave = os.environ.get("DEEPSEEK_API_KEY")
    if clave:
        return clave.strip()
    archivo = Path(__file__).resolve().parent.parent / "ia.key"
    if archivo.exists():
        valor = archivo.read_text(encoding="utf-8").strip()
        return valor or None
    return None


def resolucion_enviada(ruta_imagen: str, detail: str = "auto") -> dict:
    """Qué resolución recibe el proveedor de visión.

    Devuelve el tamaño real del archivo y el que se procesará según `detail`:

      • "auto" / "high" → se procesa a tamaño real (el ROI tal cual)
      • "low"           → el proveedor la redimensiona a un cuadrado de
                         512x512, así que un recorte apaisado se distorsiona

    El ROI ya se recorta antes de llegar aquí, así que "tamaño real" es el
    del recorte, no el del frame completo de la cámara.
    """
    info = {"detail": detail}
    try:
        import cv2
        img = cv2.imread(str(ruta_imagen))
        if img is None:
            return info
        alto, ancho = img.shape[:2]
    except Exception:  # noqa: BLE001 — es solo informativo
        return info

    info["archivo"] = [ancho, alto]
    if detail == "low":
        info["procesada"] = [512, 512]
        info["distorsionada"] = (round(512 / ancho, 2) != round(512 / alto, 2))
    else:
        info["procesada"] = [ancho, alto]
        info["distorsionada"] = False
    return info


def analizar_imagen(ruta_imagen: str, api_key: str | None = None,
                    model: str = MODELO_DEFECTO,
                    prompt: str | None = None,
                    detail: str = "low") -> dict:
    """Envía la imagen a DeepSeek Vision y devuelve el JSON que la IA
    produzca siguiendo el `prompt` (system). Es un motor genérico: la
    estructura de respuesta la define el prompt, NO el backend.

    `detail` controla el tamaño con que se procesa la imagen:

      • "low"  → se escala a 512×512. Más rápido y económico, pero pierde
                  detalle fino (números o texto pequeño pueden ser ilegibles).
      • "high" → mantiene la resolución original. Necesario para leer
                  displays con dígitos o texto pequeño.
      • "auto" → equivale a "high" hoy.

    Nunca lanza: ante error devuelve un dict {'error': ...}.
    """
    clave = api_key or _obtener_clave()
    if not clave:
        return {"error": "No hay DEEPSEEK_API_KEY (env o archivo ia.key)"}

    try:
        with open(ruta_imagen, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
    except OSError as e:
        return {"error": f"No se pudo leer la imagen: {e}"}

    # Resolución del archivo que se envía y la que el proveedor procesará.
    envio = resolucion_enviada(ruta_imagen, detail)

    mime = "image/png"
    if ruta_imagen.lower().endswith((".jpg", ".jpeg")):
        mime = "image/jpeg"
    elif ruta_imagen.lower().endswith((".webp")):
        mime = "image/webp"

    # El sistema = INSTRUCCION_JSON (neutro) + el prompt del usuario.
    # El prompt del usuario define qué campos/estructura quiere.
    sys_text = prompt or _obtener_prompt_archivo()
    if INSTRUCCION_JSON not in sys_text:
        sys_text = INSTRUCCION_JSON + "\n\n" + sys_text

    cuerpo = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_text},
            {"role": "user", "content": [
                {"type": "text", "text": "Analiza la imagen según tus "
                                            "instrucciones y responde el JSON."},
                {"type": "image_url", "image_url": {
                    "url": f"data:{mime};base64,{b64}",
                    # Escala el procesamiento de la imagen: "low" la reduce a
                    # 512x512 y "high" mantiene la resolución. Sin este campo
                    # el proveedor asume su propio valor por defecto.
                    "detail": detail if detail in ("low", "high", "auto")
                              else "low"}},
            ]},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }

    import json as _json
    json_bytes = _json.dumps(cuerpo).encode("utf-8")

    import urllib.request
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {clave}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            datos = _json.loads(resp.read().decode("utf-8"))
        contenido = (datos.get("choices", [{}])[0]
                     .get("message", {}).get("content", ""))
        resultado = _parsear_respuesta(contenido)
        # Dejar constancia de con qué resolución se analizó: sin esto no hay
        # forma de saber si la IA leyó el ROI a tamaño real o remuestreado.
        if isinstance(resultado, dict) and "error" not in resultado:
            resultado.setdefault("_entrada", envio)
        return resultado
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Error llamando a DeepSeek Vision: {e}")
        return {"error": f"Error de API: {e}"}


def _parsear_respuesta(contenido: str) -> dict:
    """Extrae el JSON que devuelve la IA y lo conserva tal cual
    (mantiene claves y orden). Devuelve {} si no puede parsear."""
    texto = contenido or ""
    if "```" in texto:
        for b in texto.split("```"):
            b = b.strip()
            if b.startswith("json"):
                b = b[4:].strip()
            if b.startswith("{"):
                texto = b
                break
    try:
        inicio = texto.find("{")
        fin = texto.rfind("}")
        if inicio == -1 or fin == -1:
            raise ValueError("sin objeto JSON")
        return json.loads(texto[inicio:fin + 1])
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"No se pudo parsear JSON de la IA: {e} | raw={texto[:300]}")
        return {"error_parseo": str(e), "respuesta_cruda": texto[:500]}


# Instrucciones que agrega el sistema de forma neutra: le pide a la IA
# que SIEMPRE responda en JSON, pero SIN imponer qué campos usar — esos
# los define el prompt del usuario en ia_prompt.txt.
INSTRUCCION_JSON = (
    "IMPORTANTE: responde únicamente en JSON válido (objeto), sin texto "
    "adicional fuera del JSON, y usa exactamente los campos y el orden "
    "que se te piden. No agregues ni omitas campos de la estructura pedida."
)


# ── Prompt editable del usuario ────────────────────────────────────

_RUTA_PROMPT = None  # se resuelve al primer uso


def _padre_raiz():
    global _RUTA_PROMPT
    if _RUTA_PROMPT is None:
        from pathlib import Path
        _RUTA_PROMPT = Path(__file__).resolve().parent.parent / "ia_prompt.txt"
    return _RUTA_PROMPT


def _obtener_prompt_archivo() -> str:
    """Devuelve el prompt guardado en ia_prompt.txt; si no existe,
    lo crea con el PROMPT_SISTEMA por defecto y lo devuelve."""
    ruta = _padre_raiz()
    if ruta.exists():
        try:
            txt = ruta.read_text(encoding="utf-8").strip()
            if txt:
                return txt
        except OSError:
            pass
    # Crear el archivo con el default si no existe
    try:
        ruta.write_text(PROMPT_SISTEMA, encoding="utf-8")
    except OSError:
        pass
    return PROMPT_SISTEMA


def lee_prompt_actual() -> str:
    return _obtener_prompt_archivo()


def guarda_prompt(texto: str) -> None:
    ruta = _padre_raiz()
    ruta.write_text(texto, encoding="utf-8")


# ── Bloqueo del prompt con contraseña ──────────────────────────────
#
# La contraseña vive en `prompt.key` (gitignored, igual que ia.key).
# Si NO existe, el prompt queda libre (sin protección). Si existe,
# las rutas que modifican el prompt o su historial exigen la cabecera
# `X-Prompt-Key` y devuelven 401 si no coincide.

import hmac as _hmac
import os as _os


class PromptBloqueado(Exception):
    """Se lanza cuando falta o no coincide la contraseña del prompt."""


def _ruta_clave_prompt():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "prompt.key"


def clave_prompt_configurada() -> bool:
    """True si hay una contraseña definida para proteger el prompt."""
    ruta = _ruta_clave_prompt()
    try:
        return bool(ruta.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def _clave_guardada() -> str:
    ruta = _ruta_clave_prompt()
    if not ruta.exists():
        return ""
    # Permite definirla también por variable de entorno
    env = _os.environ.get("PROMPT_KEY", "").strip()
    if env:
        return env
    try:
        return ruta.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def verifica_clave_prompt(clave: str | None) -> bool:
    """True si la clave enviada es válida.

    Si no hay contraseña configurada, siempre autoriza (modo libre).
    Si la hay, exige coincidencia exacta (comparación en tiempo constante).
    """
    guardada = _clave_guardada()
    if not guardada:
        return True
    return _hmac.compare_digest(guardada, (clave or "").strip())


def exige_clave_prompt(clave: str | None) -> None:
    """Lanza PromptBloqueado si la clave no es válida."""
    if not verifica_clave_prompt(clave):
        raise PromptBloqueado("Contraseña incorrecta")


# ── Historial de prompts del usuario (máx 10) ──────────────────────

HIST_MAX = 10


def _ruta_hist_prompts():
    from pathlib import Path
    return Path(__file__).resolve().parent.parent / "ia_prompts_hist.json"


def lista_prompts_hist(limite: int = HIST_MAX) -> list:
    """Devuelve los prompts guardados, más reciente primero.

    NOTA: el contenido es sensible (define el comportamiento de la IA),
    así que la API solo lo expone a quien tenga la contraseña."""
    ruta = _ruta_hist_prompts()
    if not ruta.exists():
        return []
    try:
        import json as _j
        datos = _j.loads(ruta.read_text(encoding="utf-8"))
        if not isinstance(datos, list):
            return []
        return datos[:limite]
    except (OSError, ValueError):
        return []


def guarda_prompt_historico(texto: str) -> list:
    """Agrega el prompt al historial (sin duplicados), conservando máx
    HIST_MAX. Devuelve la lista más reciente primero."""
    import json as _j
    ruta = _ruta_hist_prompts()
    histor = lista_prompts_hist(HIST_MAX + 10)
    if histor and histor[0] == texto:
        return histor[:HIST_MAX]
    limpio = [texto]
    for h in histor:
        if h != texto:
            limpio.append(h)
    limpio = limpio[:HIST_MAX]
    ruta.write_text(_j.dumps(limpio, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return limpio


def elimina_prompt_historico(texto: str) -> list:
    import json as _j
    ruta = _ruta_hist_prompts()
    histor = [h for h in lista_prompts_hist(HIST_MAX + 10) if h != texto]
    ruta.write_text(_j.dumps(histor[:HIST_MAX], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return histor[:HIST_MAX]
