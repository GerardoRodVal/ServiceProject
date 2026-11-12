"""Agenda en línea: el cliente pide su cita sin sesión con el enlace del negocio; el administrador la confirma.
La página solo ofrece los horarios libres y nunca muestra las citas de otros."""
from datetime import date, datetime, timedelta

from app.solicitudes import disponibles, formato

CONFIG = {
    "negocio": {"nombre": "Desarrolladora Prueba", "tipo": "desarrollo"},
    "zonas": [{"id": "el-pedregal", "nombre": "El Pedregal", "seguridad": 5}],
    "propiedades": [
        {"id": "modelo-encino", "nombre": "Modelo Encino", "zona": "el-pedregal", "precio": 1890000, "estatus": "disponible",
         "predial": 2800, "motivo_venta": "secreto"},
        {"id": "modelo-vendido", "nombre": "Modelo Vendido", "zona": "el-pedregal", "precio": 1, "estatus": "vendido"},
        {"id": "casa-suelta", "nombre": "Casa Suelta", "zona": "", "precio": 2000000, "estatus": "apartado"},
    ],
    "score": {"umbral_alta": 70}, "formulario": {"campos": []},
    # Todos los días, de 9:00 a 18:00 cada hora, una cita a la vez.
    "cita_publica": {"titulo": "Visita El Pedregal", "mensaje": "Te confirmamos por WhatsApp.", "dias": [0, 1, 2, 3, 4, 5, 6],
                     "hora_inicio": "09:00", "hora_fin": "18:00", "duracion": 60, "capacidad": 1, "dias_adelante": 10},
}
MANANA = (date.today() + timedelta(days=2)).isoformat()     # dos días: lejos de la hora actual en cualquier zona horaria


def publicar(cliente, n, config=CONFIG):
    assert cliente.put("/api/config", json=config, headers=n.admin).status_code == 200


def token_de(cliente, n):
    return cliente.get("/api/app/config", headers=n.capturista).json()["negocio"]["token_publico"]


def solicitud(**extra):
    base = {"nombre": "Ana Ruiz", "tel": "+52 1 (449) 555-0101", "zona": "el-pedregal", "casa": "modelo-encino",
            "fecha": MANANA, "horario": "16:00", "comentarios": "ya no se guarda", "asesor": "laura-gomez"}
    base.update(extra)
    return base


def pendientes(cliente, n):
    r = cliente.get("/api/solicitudes", headers=n.admin)
    assert r.status_code == 200, r.text
    return r.json()["solicitudes"]


def horas_libres(cliente, token, fecha=MANANA):
    dias = cliente.get(f"/api/publico/{token}/cita").json()["disponibles"]
    return next((d["horas"] for d in dias if d["fecha"] == fecha), [])


def test_la_pagina_trae_formato_y_catalogo_sin_datos_internos(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    r = cliente.get(f"/api/publico/{token_de(cliente, n)}/cita")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["titulo"] == "Visita El Pedregal" and d["mensaje"] == "Te confirmamos por WhatsApp."
    assert "pedir_comentarios" not in d and d["duracion"] == 60
    assert {"fecha": MANANA, "horas": [f"{h:02d}:00" for h in range(9, 19)]} in d["disponibles"]
    assert d["negocio"] == {"nombre": "Desarrolladora Prueba", "tipo": "desarrollo"}
    assert d["zonas"] == [{"id": "el-pedregal", "nombre": "El Pedregal", "casas": [{"id": "modelo-encino", "nombre": "Modelo Encino"}]}]
    assert d["sueltas"] == [{"id": "casa-suelta", "nombre": "Casa Suelta"}]          # la vendida no aparece
    for privado in ("1890000", "predial", "secreto", "umbral_alta", "seguridad", "Modelo Vendido"):
        assert privado not in r.text, privado
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["x-robots-tag"] == "noindex"


def test_sin_formato_guardado_usa_el_texto_por_omision(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n, {k: v for k, v in CONFIG.items() if k != "cita_publica"})
    d = cliente.get(f"/api/publico/{token_de(cliente, n)}/cita").json()
    assert d["titulo"] == "Agenda tu visita" and "WhatsApp" in d["mensaje"]
    # Horario por omisión: lunes a sábado de 10:00 a 19:00; el domingo no aparece.
    assert d["disponibles"] and all(date.fromisoformat(x["fecha"]).weekday() != 6 for x in d["disponibles"])
    assert all(set(x["horas"]) <= {f"{h}:00" for h in range(10, 20)} for x in d["disponibles"])


def test_el_cliente_agenda_y_el_admin_la_ve(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    r = cliente.post(f"/api/publico/{token_de(cliente, n)}/cita", json=solicitud())
    assert r.status_code == 200, r.text
    [s] = pendientes(cliente, n)
    assert s["nombre"] == "Ana Ruiz" and s["tel"] == "4495550101" and s["estado"] == "pendiente"
    assert s["zona"] == "el-pedregal" and s["casa"] == "modelo-encino" and s["fecha"] == MANANA and s["horario"] == "16:00"
    assert "comentarios" not in s and s["asesor"] == "laura-gomez"           # la página ya no pide comentarios
    assert cliente.get("/api/solicitudes", headers=n.capturista).status_code == 403
    assert cliente.get("/api/solicitudes").status_code == 401


def test_el_mismo_telefono_no_se_duplica_mientras_esta_por_confirmar(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    token = token_de(cliente, n)
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud()).status_code == 200
    # Puede volver a pedir su misma hora (su propia solicitud no se la ocupa) o cambiarla.
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud(nombre="Ana R.")).status_code == 200
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud(nombre="Ana R.", horario="10:00")).status_code == 200
    [s] = pendientes(cliente, n)
    assert s["nombre"] == "Ana R." and s["horario"] == "10:00"


def test_se_valida_contra_el_catalogo(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    url = f"/api/publico/{token_de(cliente, n)}/cita"
    # Todo es obligatorio: nombre, teléfono, propiedad, día y hora.
    for malo in (solicitud(tel="123"), solicitud(nombre=" "), solicitud(casa="modelo-vendido"), solicitud(casa="", zona="otra"),
                 solicitud(casa="", zona=""), solicitud(casa=""), solicitud(fecha=""), solicitud(horario=""),
                 solicitud(fecha="2020-01-01"), solicitud(horario="tarde"), solicitud(horario="25:00")):
        assert cliente.post(url, json=malo).status_code == 422, malo
    # Una hora con forma válida pero fuera del horario de atención: ya no está libre.
    assert cliente.post(url, json=solicitud(horario="20:00")).status_code == 409
    # Solo la casa: la zona se toma de la casa.
    assert cliente.post(url, json=solicitud(zona="", tel="4495550102")).status_code == 200
    assert {s["zona"] for s in pendientes(cliente, n)} == {"el-pedregal"}


def test_campo_trampa_y_enlace_que_no_vale(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    assert cliente.post(f"/api/publico/{token_de(cliente, n)}/cita", json=solicitud(sitio="http://spam")).status_code == 200
    assert pendientes(cliente, n) == []                                       # el robot no deja nada
    assert cliente.get("/api/publico/" + "0" * 32 + "/cita").status_code == 404
    assert cliente.post("/api/publico/no-es-token/cita", json=solicitud()).status_code == 404


def test_agenda_apagada_no_abre(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n, {**CONFIG, "cita_publica": {**CONFIG["cita_publica"], "activa": False}})
    token = token_de(cliente, n)
    assert cliente.get(f"/api/publico/{token}/cita").status_code == 404
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud()).status_code == 404


def test_el_admin_confirma_descarta_y_reabre(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    token = token_de(cliente, n)
    cliente.post(f"/api/publico/{token}/cita", json=solicitud())
    [s] = pendientes(cliente, n)
    r = cliente.patch(f"/api/solicitudes/{s['id']}", json={"estado": "confirmada", "confirmada_para": f"{MANANA}T16:00:00-06:00",
                                                          "nota": "Confirmada por WhatsApp"}, headers=n.admin)
    assert r.status_code == 200, r.text
    assert r.json()["estado"] == "confirmada" and r.json()["confirmada_para"] and r.json()["nota"] == "Confirmada por WhatsApp"
    assert cliente.patch(f"/api/solicitudes/{s['id']}", json={"estado": "rara"}, headers=n.admin).status_code == 422
    assert cliente.patch(f"/api/solicitudes/{s['id']}", json={"confirmada_para": "mañana"}, headers=n.admin).status_code == 422
    # Ya confirmada, el mismo teléfono puede volver a pedir (otra hora): queda una nueva por confirmar.
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud(horario="11:00")).status_code == 200
    assert sorted(x["estado"] for x in pendientes(cliente, n)) == ["confirmada", "pendiente"]
    # Reabrir la confirmada choca con la que ya está por confirmar.
    assert cliente.patch(f"/api/solicitudes/{s['id']}", json={"estado": "pendiente"}, headers=n.admin).status_code == 409


def test_aislamiento_entre_negocios(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    publicar(cliente, a)
    publicar(cliente, b)
    cliente.post(f"/api/publico/{token_de(cliente, a)}/cita", json=solicitud())
    [s] = pendientes(cliente, a)
    assert pendientes(cliente, b) == []
    assert cliente.patch(f"/api/solicitudes/{s['id']}", json={"estado": "descartada"}, headers=b.admin).status_code == 404


def test_el_gestor_puede_reenviar_solicitudes(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    assert cliente.post("/api/solicitudes", json=solicitud(), headers=n.admin).status_code == 200
    assert cliente.post("/api/solicitudes", json=solicitud(horario=""), headers=n.admin).status_code == 422
    assert cliente.post("/api/solicitudes", json=solicitud(casa="no-existe"), headers=n.admin).status_code == 422
    assert cliente.post("/api/solicitudes", json=solicitud(), headers=n.capturista).status_code == 403
    assert len(pendientes(cliente, n)) == 1


def test_freno_contra_envios_en_rafaga(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    url = f"/api/publico/{token_de(cliente, n)}/cita"
    codigos = [cliente.post(url, json=solicitud(tel=f"44955501{i:02d}", horario=f"{9 + i:02d}:00")).status_code for i in range(7)]
    assert codigos[:5] == [200] * 5 and codigos[5:] == [429, 429]


def test_solo_se_ofrecen_los_espacios_libres_sin_datos_de_nadie(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n)
    token = token_de(cliente, n)
    assert "16:00" in horas_libres(cliente, token)
    # Una solicitud por confirmar ya aparta su hora: otro cliente no la ve ni la puede pedir.
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud()).status_code == 200
    assert "16:00" not in horas_libres(cliente, token)
    r = cliente.post(f"/api/publico/{token}/cita", json=solicitud(tel="4495550199", nombre="Otro Cliente"))
    assert r.status_code == 409 and "disponible" in r.json()["detail"]
    # La confirmada por el admin a otra hora (fuera de la cuadrícula) ocupa los espacios con que se empalma.
    [s] = pendientes(cliente, n)
    cliente.patch(f"/api/solicitudes/{s['id']}", json={"estado": "confirmada", "confirmada_para": f"{MANANA}T12:30:00-06:00"},
                  headers=n.admin)
    libres = horas_libres(cliente, token)
    assert "12:00" not in libres and "13:00" not in libres and "16:00" in libres
    # La página nunca trae nombres ni teléfonos de otros.
    texto = cliente.get(f"/api/publico/{token}/cita").text
    assert "Ana" not in texto and "4495550101" not in texto


def test_varias_citas_al_mismo_tiempo_si_la_capacidad_lo_permite(cliente, crear_negocio):
    n = crear_negocio()
    publicar(cliente, n, {**CONFIG, "cita_publica": {**CONFIG["cita_publica"], "capacidad": 2}})
    token = token_de(cliente, n)
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud()).status_code == 200
    assert "16:00" in horas_libres(cliente, token)
    assert cliente.post(f"/api/publico/{token}/cita", json=solicitud(tel="4495550199")).status_code == 200
    assert "16:00" not in horas_libres(cliente, token)


def test_calculo_de_disponibles():
    f = formato({"cita_publica": {"dias": [0, 1, 2, 3, 4], "hora_inicio": "10:00", "hora_fin": "12:00", "duracion": 60,
                                  "dias_adelante": 6}})
    lunes = datetime(2026, 11, 2, 8, 0)                                   # lunes 8:00
    dias = disponibles(f, ["2026-11-02T11:00", "2026-11-03T10:30", "basura"], lunes)
    assert [d["fecha"] for d in dias] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]   # sin fin de semana
    assert dias[0]["horas"] == ["10:00", "12:00"]                         # la de las 11 está ocupada
    assert dias[1]["horas"] == ["12:00"]                                  # la de 10:30 se empalma con las 10 y las 11
    # Con menos de una hora de aviso no se ofrece el espacio.
    assert disponibles(f, [], datetime(2026, 11, 2, 9, 30))[0]["horas"] == ["11:00", "12:00"]
    # Valores fuera de rango vuelven al horario por omisión.
    malo = formato({"cita_publica": {"dias": [], "hora_inicio": "18:00", "hora_fin": "09:00", "duracion": 7, "capacidad": 0}})
    assert (malo["dias"], malo["hora_inicio"], malo["duracion"], malo["capacidad"]) == ([0, 1, 2, 3, 4, 5], "10:00", 60, 1)
