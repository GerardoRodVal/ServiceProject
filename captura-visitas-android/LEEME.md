# Captura de visita — APK para Android

App mínima que **solo captura visitas**. Es la misma pantalla de `../captura-visitas-pwa/index.html`
envuelta en una app. No incluye taller, seguimiento ni el gestor de escritorio.

Las visitas se guardan en el celular y se envían **a la PC por Wi-Fi** (el gestor las guarda en SQLite) **o a la nube**
(API en Azure, con inicio de sesión de la cuenta de cada asesor). Si no hay señal, quedan pendientes y se envían al volver la red.

Versión 1.1.0: trabaja con la nube (ver `../nube/README.md`, sección «La app Android (APK) con la nube») e incluye el lector
de QR de la pestaña Casa, que la 1.0.0 no traía.

## Compilar la APK

Requisitos: JDK 17 y Android SDK con `platforms;android-35` y `build-tools;35.0.0`.

```bash
powershell -ExecutionPolicy Bypass -File .\build-apk.ps1
```

La APK queda en `dist\CapturaVisitas-1.1.0.apk`.

Pruebas de las reglas de destinos (Java a secas, sin Android; sale con código 1 si algo falla):

```bash
javac -encoding UTF-8 -d build/prueba app/src/main/java/com/nearustech/capturavisitas/Destinos.java app/src/test/java/com/nearustech/capturavisitas/DestinosPrueba.java
java -cp build/prueba com.nearustech.capturavisitas.DestinosPrueba
```

- Con la llave de publicación (`%USERPROFILE%\.android-keys\captura-visitas.properties`) la APK sale firmada para el cliente.
- Sin esa llave, firma con la llave de depuración: sirve para probar, no para entregar.

El proyecto también abre en Android Studio (`Build > Build APK(s)`). Gradle no se ha probado en la PC
donde se armó esta versión, así que usa el script si Gradle falla.

## Primer uso en el celular

1. Instalar la APK.
2. Al abrir, escribir la dirección:
   - **PC:** la que muestra el gestor (pestaña **Conexión**), por ejemplo `http://192.168.1.50:8765/index.html`. Debe incluir el puerto.
   - **Nube:** la dirección `https://…` que te dio tu administrador. Pulsa **Entrar**: se abre el navegador del teléfono para
     iniciar sesión y, al terminar, vuelves a la app.
3. La app descarga el formulario y la lista de propiedades. Después, la última configuración queda guardada en el celular.
4. Para cambiar la dirección: menú ⋮ > **Dirección de la PC o la nube** (si cambia de servidor, la app se recarga).

## Qué no cambia

- El formulario se sigue configurando en el gestor de la PC (sección **Registro**). La app lo toma al abrirse y cada minuto con señal.
- El celular debe estar en la misma red Wi-Fi que la PC para enviar.

## Archivos

- `app/src/main/java/.../MainActivity.java`: ventana con la captura y el menú de dirección.
- `app/src/main/java/.../PuenteNativo.java`: hace las peticiones desde el lado nativo. Solo acepta la dirección guardada y, en modo nube, el proveedor de inicio de sesión que esa nube declara.
- `app/src/main/java/.../Destinos.java`: las reglas de a dónde puede hablar la app (sin Android; probadas en `app/src/test`).
- `app/src/main/AndroidManifest.xml`, `app/src/main/res/`: configuración y recursos.
- `build-apk.ps1`: compila, firma y verifica la APK.
- `../captura-visitas-pwa/index.html`: la pantalla de captura. Se copia al compilar; no se mantiene una copia aparte.
