"""Fotos del catálogo: las sube el administrador (el gestor) y las ve cualquiera que tenga su identificador."""
import uuid

from .conftest import PNG

JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def nuevo_id():
    return uuid.uuid4().hex


def subir(cliente, cabecera, id_, datos=JPG):
    return cliente.put(f"/api/imagen/{id_}", content=datos, headers={**(cabecera or {}), "Content-Type": "application/octet-stream"})


def blobs_de(cliente, negocio_id):
    return [b.name for b in cliente.app.state.almacen.contenedor.list_blobs(name_starts_with=f"{negocio_id}/")]


def test_el_administrador_sube_una_foto_y_cualquiera_con_su_id_la_ve(cliente, crear_negocio):
    n = crear_negocio()
    i = nuevo_id()
    assert subir(cliente, n.admin, i).json() == {"ok": True, "existia": False}
    assert subir(cliente, n.admin, i).json() == {"ok": True, "existia": True}            # repetirlo no hace nada ni duplica
    r = cliente.get(f"/api/imagen/{i}")                                                   # sin sesión: es una etiqueta <img>
    assert r.status_code == 200 and r.content == JPG and r.headers["content-type"] == "image/jpeg"
    assert "public" in r.headers["cache-control"] and "immutable" in r.headers["cache-control"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert len(blobs_de(cliente, n.id)) == 1


def test_png_se_acepta_y_se_sirve_con_su_tipo(cliente, crear_negocio):
    n = crear_negocio()
    i = nuevo_id()
    assert subir(cliente, n.admin, i, PNG).status_code == 200
    assert cliente.get(f"/api/imagen/{i}").headers["content-type"] == "image/png"


def test_solo_el_administrador_sube_y_solo_imagenes_con_tope(cliente, crear_negocio):
    n = crear_negocio()
    i = nuevo_id()
    assert subir(cliente, n.capturista, i).status_code == 403
    assert subir(cliente, None, i).status_code == 401
    assert subir(cliente, n.admin, i, b"esto no es una imagen").status_code == 415
    assert subir(cliente, n.admin, i, JPG + b"\x00" * (4 * 1024 * 1024)).status_code == 413
    assert subir(cliente, n.admin, "no-es-un-id").status_code == 404
    assert subir(cliente, n.admin, "A" * 32).status_code == 404                           # el id va en minúsculas
    assert cliente.get(f"/api/imagen/{i}").status_code == 404                              # nada de eso dejó una foto
    assert blobs_de(cliente, n.id) == []


def test_las_fotos_que_no_existen_o_con_id_raro_dan_404(cliente):
    for i in (nuevo_id(), "x" * 32, "1234", "../" + "0" * 29, "0" * 33):
        assert cliente.get(f"/api/imagen/{i}").status_code == 404, i


def test_un_negocio_no_puede_quedarse_con_el_id_de_otro(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    i = nuevo_id()
    subir(cliente, a.admin, i, JPG)
    r = subir(cliente, b.admin, i, JPG + b"intruso")
    assert r.status_code == 409 and "ya está en uso" in r.json()["detail"]
    assert cliente.get(f"/api/imagen/{i}").content == JPG                                 # sigue siendo la de A
    assert blobs_de(cliente, b.id) == []                                                   # y no quedó un archivo huérfano de B


def test_las_fotos_del_catalogo_no_salen_por_la_ruta_de_archivos_de_las_visitas(cliente, crear_negocio):
    n = crear_negocio()
    i = nuevo_id()
    subir(cliente, n.admin, i)
    assert cliente.get(f"/api/archivos/{uuid.UUID(hex=i)}", headers=n.admin).status_code == 404


def test_sincronizar_dice_cuales_faltan_y_borra_las_que_sobran(cliente, crear_negocio):
    a, b = crear_negocio(), crear_negocio()
    x, y, z, w = nuevo_id(), nuevo_id(), nuevo_id(), nuevo_id()
    subir(cliente, a.admin, x)
    subir(cliente, a.admin, y)
    subir(cliente, b.admin, w)
    r = cliente.post("/api/imagenes/sincronizar", json={"ids": [x, z]}, headers=a.admin)
    assert r.json() == {"faltan": [z], "borradas": 1}                                      # y ya no está en el catálogo
    assert cliente.get(f"/api/imagen/{y}").status_code == 404 and cliente.get(f"/api/imagen/{x}").status_code == 200
    assert len(blobs_de(cliente, a.id)) == 1                                                # el archivo de y también se borró
    assert cliente.get(f"/api/imagen/{w}").status_code == 200                              # la de otro negocio no se toca
    assert cliente.post("/api/imagenes/sincronizar", json={"ids": []}, headers=a.admin).json() == {"faltan": [], "borradas": 1}
    assert cliente.get(f"/api/imagen/{w}").status_code == 200


def test_sincronizar_valida_ids_y_permisos(cliente, crear_negocio):
    n = crear_negocio()
    assert cliente.post("/api/imagenes/sincronizar", json={"ids": ["no-valido"]}, headers=n.admin).status_code == 422
    assert cliente.post("/api/imagenes/sincronizar", json={"ids": []}, headers=n.capturista).status_code == 403
    assert cliente.post("/api/imagenes/sincronizar", json={"ids": []}).status_code == 401
    assert cliente.post("/api/imagenes/sincronizar", json={"ids": [nuevo_id() for _ in range(501)]}, headers=n.admin).status_code == 422


def test_las_fotos_cuentan_en_el_uso_de_almacenamiento(cliente, crear_negocio):
    n = crear_negocio()
    subir(cliente, n.admin, nuevo_id(), JPG + b"\x00" * (1024 * 1024))
    assert cliente.get("/api/uso", headers=n.admin).json()["archivos_mb_mes"] == 1.0
