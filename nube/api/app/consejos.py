"""Consejos del celular, adaptados a las visitas de cada asesor. Mismos textos y misma lógica que asesor_app.consejos del
gestor de Windows (gestor-windows/asesor_app.py), para que la app diga lo mismo con el gestor y con la nube."""
from fastapi import APIRouter, Depends

from . import db
from .auth import requiere

router = APIRouter(prefix="/api/app")
ambos_roles = requiere("admin", "capturista")

PUNTOS_DEFECTO = {"umbral_alta": 70, "presupuesto_cubre": 30, "credito_preaprobado": 20, "enganche_alto": 15,
                  "enganche_alto_pct": 20, "plazo_corto": 20, "decide_solo": 15}


def construir(altas, mejor, medias, score):
    """altas: prospectos de prioridad alta sin contactar; mejor: nombre del que más puntos tiene (o ''); medias: prospectos de
    prioridad media sin crédito; score: pesos del negocio (los que falten se completan con los de siempre)."""
    s = {k: (score or {}).get(k, v) for k, v in PUNTOS_DEFECTO.items()}
    lista = []
    if altas:
        plural = "prospecto de prioridad alta" if altas == 1 else "prospectos de prioridad alta"
        lista.append({"urgente": True, "tag": "Misión del día",
                      "texto": f"Tienes {altas} {plural} sin contactar. Llama primero a {mejor or 'quien más puntos tiene'}: es quien más puntos tiene."})
    if medias:
        plural = "prospecto de prioridad media no tiene" if medias == 1 else "prospectos de prioridad media no tienen"
        lista.append({"tag": "Sube de nivel",
                      "texto": f"{medias} {plural} crédito. Ayúdales a precalificar: un crédito preaprobado suma +{s['credito_preaprobado']} puntos y puede subirlos a prioridad alta."})
    lista += [
        {"tag": "Prospecto alto", "texto": f"Un prospecto es de prioridad alta desde {s['umbral_alta']} puntos. Lo que más suma: presupuesto que cubre el precio (+{s['presupuesto_cubre']}) y crédito preaprobado (+{s['credito_preaprobado']})."},
        {"tag": "Prospecto alto", "texto": f"Pregunta cuánto tiene para el enganche: con {s['enganche_alto_pct']}% o más suma +{s['enganche_alto']} puntos y el cierre es más rápido."},
        {"tag": "Prospecto alto", "texto": f"Pregunta para cuándo necesita mudarse. Si es en menos de 3 meses suma +{s['plazo_corto']} puntos: es alguien listo para decidir."},
        {"tag": "Cómo vender", "texto": "Responde rápido: un prospecto que recibe llamada el mismo día de su visita recuerda mejor la propiedad y agenda con más facilidad."},
        {"tag": "Cómo vender", "texto": "Termina cada visita con un siguiente paso con fecha: segunda visita, envío de cotización o cita con el banco."},
        {"tag": "Cómo vender", "texto": "Anota las objeciones en «Notas» durante la visita: con ellas preparas la siguiente llamada y respondes justo lo que le preocupa."},
    ]
    return lista[:6]


@router.get("/consejos")
def consejos(ident=Depends(ambos_roles)):
    """Consejos para quien inició sesión: se calculan con SUS visitas (las que capturó), no con las de todo el negocio."""
    with db.transaccion(ident.negocio_id) as con:
        conf = con.execute("SELECT datos->'score' AS score FROM configuraciones WHERE negocio_id = %s", (ident.negocio_id,)).fetchone()
        cuentas = con.execute(
            """SELECT count(*) FILTER (WHERE datos->>'cat' = 'alta' AND estado = 'nuevo') AS altas,
                      count(*) FILTER (WHERE datos->>'cat' = 'media' AND datos->>'credito' = 'no') AS medias
               FROM registros WHERE datos->>'asesor_id' = %s""", (ident.usuario_id,)).fetchone()
        # El de más puntos entre los de prioridad alta sin contactar; a igualdad, el más reciente (como en el gestor).
        mejor = con.execute(
            """SELECT datos->>'nombre' AS nombre FROM registros
               WHERE datos->>'asesor_id' = %s AND datos->>'cat' = 'alta' AND estado = 'nuevo'
               ORDER BY CASE WHEN datos->>'score' ~ '^[0-9]+$' THEN (datos->>'score')::int ELSE 0 END DESC, creado DESC LIMIT 1""",
            (ident.usuario_id,)).fetchone()
    score = conf["score"] if conf and isinstance(conf["score"], dict) else {}
    return {"consejos": construir(cuentas["altas"], mejor["nombre"] if mejor else "", cuentas["medias"], score)}
