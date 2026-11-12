"""Archivos (fotos) en Blob Storage: Azurite en local, una cuenta de Azure Storage en la nube.
En Azure se usa identidad administrada (BLOB_ACCOUNT_URL): no hay llaves que guardar ni rotar."""
import uuid

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobServiceClient, ContentSettings

TIPOS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_BYTES = 5 * 1024 * 1024


def tipo_por_contenido(datos: bytes):
    """Tipo real según los primeros bytes. No se confía en el Content-Type que declara quien sube el archivo."""
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


class Almacen:
    def __init__(self, cfg):
        if cfg.blob_connection_string:
            servicio = BlobServiceClient.from_connection_string(cfg.blob_connection_string)
        else:
            from azure.identity import DefaultAzureCredential
            servicio = BlobServiceClient(cfg.blob_account_url, credential=DefaultAzureCredential())
        self.contenedor = servicio.get_container_client(cfg.blob_contenedor)
        if cfg.blob_crear_contenedor:
            try:
                self.contenedor.create_container()
            except ResourceExistsError:
                pass

    def guardar(self, negocio_id, datos: bytes, tipo: str):
        # El nombre empieza con el negocio: facilita borrar todo lo de un cliente y revisar cuánto ocupa.
        nombre = f"{negocio_id}/{uuid.uuid4().hex}{TIPOS[tipo]}"
        self.contenedor.upload_blob(nombre, datos, content_settings=ContentSettings(content_type=tipo))
        return nombre

    def leer(self, nombre):
        try:
            return self.contenedor.download_blob(nombre).readall()
        except ResourceNotFoundError:
            return None

    def guardar_como(self, nombre, datos: bytes, tipo: str):
        self.contenedor.upload_blob(nombre, datos, overwrite=True, content_settings=ContentSettings(content_type=tipo))

    def borrar(self, nombre):
        try:
            self.contenedor.delete_blob(nombre)
        except ResourceNotFoundError:
            pass

    def borrar_prefijo(self, prefijo):
        """Solo para pruebas y baja de clientes."""
        for blob in self.contenedor.list_blobs(name_starts_with=prefijo):
            self.contenedor.delete_blob(blob.name)
