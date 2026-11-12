"""Citas de los asesores y su ubicación."""
import os
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

PUNTO = {"lat": 19.4326, "lng": -99.1332, "precision": 12}


def iniciar(cliente, cabecera, **extra):
    return cliente.post("/api/app/cita", json={"accion": "iniciar", **extra}, headers=cabecera)


def terminar(cliente, cabecera):
    return cliente.post("/api/app/cita", json={"accion": "terminar"}, headers=cabecera)


def ubicar(cliente, cabecera, **punto):
    return cliente.post("/api/app/ubicacion", json={**PUNTO, **punto}, headers=cabecera)


def id_de(cliente, n, correo):
    return next(u["id"] for u in cliente.get("/api/usuarios", headers=n.admin).json()["usuarios"] if u["correo"] == correo)


def test_una_cita_de_principio_a_fin(cliente, crear_negocio):
    n = crear_negocio()
    c = n.capturista
    assert cliente.get("/api/app/estado", headers=c).json() == {"en_cita": False, "inicio": None, "ubicacion": None}
    primera = iniciar(cliente, c).json()
    assert primera["en_cita"] is True and primera["inicio"] and primera["ubicacion"] is None
    assert iniciar(cliente, c).json()["inicio"] == primera["inicio"]                  # iniciar de nuevo no reinicia el cronómetro
    r = ubicar(cliente, c)
    assert r.status_code == 200 and r.json()["ubicacion"]["lat"] == PUNTO["lat"] and r.json()["inicio"] == primera["inicio"]
    assert cliente.get("/api/app/estado", headers=c).json()["ubicacion"]["precision"] == 12
    cierre = terminar(cliente, c).json()
    assert cierre["en_cita"] is False
    resumen = cierre["resumen"]
    assert resumen["duracion_seg"] >= 0 and resumen["puntos"] == 1 and resumen["ubicacion_final"]["lng"] == PUNTO["lng"]
    assert resumen["inicio"] == primera["inicio"] and resumen["asesor_id"]
    assert cliente.get("/api/app/estado", headers=c).json()["en_cita"] is False
    assert terminar(cliente, c).json() == {"en_cita": False, "resumen": None}          # terminar sin cita no falla


def test_la_ubicacion_solo_se_acepta_con_una_cita_en_curso_y_valida(cliente, crear_negocio):
    n = crear_negocio()
    c = n.capturista
    r = ubicar(cliente, c)
    assert r.status_code == 409 and "No hay una cita" in r.json()["detail"]
    iniciar(cliente, c)
    for malo in ({"lat": 91}, {"lat": -91}, {"lng": 181}, {"precision": -1}, {"lat": "norte"}):
        assert ubicar(cliente, c, **malo).status_code == 422, malo
    assert cliente.get("/api/app/estado", headers=c).json()["ubicacion"] is None      # nada se guardó


def test_cerrar_sesion_termina_la_cita_en_curso(cliente, crear_negocio):
    n = crear_negocio()
    iniciar(cliente, n.capturista)
    assert cliente.post("/api/app/salir", json={}, headers=n.capturista).json() == {"ok": True}
    assert cliente.get("/api/app/estado", headers=n.capturista).json()["en_cita"] is False
    assert cliente.post("/api/app/salir", json={}, headers=n.capturista).status_code == 200      # sin cita tampoco falla


def test_cada_asesor_tiene_su_propia_cita_y_solo_una(cliente, crear_negocio, admin_con):
    n = crear_negocio()
    iniciar(cliente, n.capturista)
    assert cliente.get("/api/app/estado", headers=n.admin).json()["en_cita"] is False
    iniciar(cliente, n.admin)
    assert terminar(cliente, n.capturista).json()["resumen"] is not None
    assert cliente.get("/api/app/estado", headers=n.admin).json()["en_cita"] is True        # la del otro sigue
    with pytest.raises(psycopg.errors.UniqueViolation):                                      # la base lo impide aunque la API fallara
        admin_con.execute("INSERT INTO citas (negocio_id, usuario_id) VALUES (%s, %s)", (n.id, id_de(cliente, n, n.admin_correo)))


def test_puntos_muy_seguidos_se_ignoran_pero_la_ultima_ubicacion_se_actualiza(cliente, crear_negocio):
    n = crear_negocio()
    c = n.capturista
    iniciar(cliente, c)
    ubicar(cliente, c, lat=10)
    ubicar(cliente, c, lat=20)                                                              # 5 s mínimo entre puntos del trayecto
    assert cliente.get("/api/app/estado", headers=c).json()["ubicacion"]["lat"] == 20
    resumen = terminar(cliente, c).json()["resumen"]
    assert resumen["puntos"] == 1 and resumen["ubicacion_final"]["lat"] == 20


def test_el_trayecto_tiene_tope_por_cita(cliente, crear_negocio, admin_con):
    n = crear_negocio()
    c = n.capturista
    iniciar(cliente, c)
    cita_id = admin_con.execute("SELECT id FROM citas WHERE negocio_id = %s", (n.id,)).fetchone()["id"]
    admin_con.execute("INSERT INTO ubicaciones (negocio_id, cita_id, capturada, lat, lng) "
                      "SELECT %s, %s, now() - interval '1 hour' + g * interval '1 second', 1, 1 FROM generate_series(1, 2000) g", (n.id, cita_id))
    assert ubicar(cliente, c, lat=50).status_code == 200
    assert admin_con.execute("SELECT count(*) AS n FROM ubicaciones WHERE cita_id = %s", (cita_id,)).fetchone()["n"] == 2000
    assert cliente.get("/api/app/estado", headers=c).json()["ubicacion"]["lat"] == 50


def test_el_inicio_declarado_sin_senal_solo_vale_si_es_razonable(cliente, crear_negocio):
    ahora = datetime.now(timezone.utc)
    for declarado, esperado_hace in ((ahora - timedelta(hours=1), 3600), (ahora + timedelta(hours=1), 0),
                                     (ahora - timedelta(hours=13), 0), ("basura", 0)):
        n = crear_negocio()
        texto = declarado if isinstance(declarado, str) else declarado.isoformat()
        inicio = datetime.fromisoformat(iniciar(cliente, n.capturista, inicio=texto).json()["inicio"])
        hace = (datetime.now(timezone.utc) - inicio).total_seconds()
        assert abs(hace - esperado_hace) < 15, (declarado, hace)


def test_el_administrador_ve_las_citas_en_curso_el_historial_y_el_trayecto(cliente, crear_negocio):
    n = crear_negocio()
    iniciar(cliente, n.capturista)
    ubicar(cliente, n.capturista)
    activas = cliente.get("/api/citas/activas", headers=n.admin).json()["citas"]
    assert len(activas) == 1 and activas[0]["en_cita"] and activas[0]["ubicacion"]["lat"] == PUNTO["lat"]
    assert activas[0]["nombre"] and activas[0]["usuario_id"] == id_de(cliente, n, n.capturista_correo)
    assert cliente.get("/api/citas", headers=n.admin).json()["citas"] == []                  # aún no hay terminadas
    terminar(cliente, n.capturista)
    assert cliente.get("/api/citas/activas", headers=n.admin).json()["citas"] == []
    historial = cliente.get("/api/citas", headers=n.admin).json()["citas"]
    assert len(historial) == 1 and historial[0]["puntos"] == 1 and historial[0]["duracion_seg"] >= 0
    trayecto = cliente.get(f"/api/citas/{historial[0]['id']}/trayecto", headers=n.admin).json()["puntos"]
    assert len(trayecto) == 1 and trayecto[0]["lat"] == PUNTO["lat"]
    # El capturista no puede ver lo de los demás.
    for ruta in ("/api/citas/activas", "/api/citas", f"/api/citas/{historial[0]['id']}/trayecto"):
        assert cliente.get(ruta, headers=n.capturista).status_code == 403


def test_un_negocio_no_ve_las_citas_ni_la_ubicacion_de_otro(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    iniciar(cliente, a.capturista)
    ubicar(cliente, a.capturista)
    terminar(cliente, a.capturista)
    iniciar(cliente, a.admin)
    cita_de_a = cliente.get("/api/citas", headers=a.admin).json()["citas"][0]["id"]
    assert cliente.get("/api/citas/activas", headers=b.admin).json()["citas"] == []
    assert cliente.get("/api/citas", headers=b.admin).json()["citas"] == []
    assert cliente.get(f"/api/citas/{cita_de_a}/trayecto", headers=b.admin).status_code == 404
    assert cliente.get("/api/app/estado", headers=b.admin).json()["en_cita"] is False


def test_el_trayecto_viejo_se_borra_por_retencion(cliente, crear_negocio, admin_con):
    n = crear_negocio()
    c = n.capturista
    usuario = id_de(cliente, n, n.capturista_correo)
    # Una cita terminada hace 45 días, con su trayecto y su ubicación final.
    vieja = admin_con.execute(
        "INSERT INTO citas (negocio_id, usuario_id, inicio, fin, ubicacion_final) VALUES (%s, %s, now() - interval '45 days', now() - interval '45 days' + interval '1 hour', %s) RETURNING id",
        (n.id, usuario, '{"lat": 1, "lng": 1}')).fetchone()["id"]
    admin_con.execute("INSERT INTO ubicaciones (negocio_id, cita_id, capturada, lat, lng) VALUES (%s, %s, now() - interval '45 days', 1, 1)", (n.id, vieja))
    iniciar(cliente, c)
    ubicar(cliente, c)
    terminar(cliente, c)                                                                      # al terminar una cita se purga lo vencido
    assert admin_con.execute("SELECT count(*) AS n FROM ubicaciones WHERE cita_id = %s", (vieja,)).fetchone()["n"] == 0
    assert admin_con.execute("SELECT ubicacion_final FROM citas WHERE id = %s", (vieja,)).fetchone()["ubicacion_final"] is None
    assert admin_con.execute("SELECT count(*) AS n FROM citas WHERE id = %s", (vieja,)).fetchone()["n"] == 1      # la cita (sin ubicación) se conserva
    assert admin_con.execute("SELECT count(*) AS n FROM ubicaciones WHERE negocio_id = %s", (n.id,)).fetchone()["n"] == 1   # el de hoy sigue


def test_la_base_no_deja_cambiar_el_inicio_ni_el_asesor_de_una_cita(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    iniciar(cliente, a.capturista)
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as con:
        con.execute("SELECT set_config('app.negocio_id', %s, false)", (a.id,))
        assert con.execute("UPDATE citas SET ultima_ubicacion = '{}'::jsonb").rowcount == 1
        for columna in ("inicio = now()", "usuario_id = gen_random_uuid()", "negocio_id = gen_random_uuid()"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                con.execute(f"UPDATE citas SET {columna}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):                              # tampoco crear citas en otro negocio
            con.execute("INSERT INTO citas (negocio_id, usuario_id) VALUES (%s, gen_random_uuid())", (b.id,))
