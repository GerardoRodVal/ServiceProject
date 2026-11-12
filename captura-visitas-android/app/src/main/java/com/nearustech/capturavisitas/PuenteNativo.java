package com.nearustech.capturavisitas;

import android.content.SharedPreferences;
import android.webkit.JavascriptInterface;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Iterator;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Puente entre la página de captura (JavaScript) y la red del teléfono.
 * Las peticiones solo pueden ir a la dirección que guardó el usuario (la PC o la nube) y, en modo nube, al proveedor de
 * inicio de sesión que esa nube declara en /app-config.json (ver Destinos): la página no puede usar este puente para
 * hablar con otros sitios.
 */
final class PuenteNativo {

    static final String NOMBRE_PREFS = "captura";
    static final String CLAVE_DESTINO = "destino";
    static final String CLAVE_CONFIG = "config_cache";
    static final String CLAVE_EMISOR = "emisor_oidc";      // origen https del proveedor de inicio de sesión de la nube
    static final String CLAVE_VUELTA = "vuelta_oidc";      // ?code=...&state=... con lo que devolvió el proveedor

    private static final int LIMITE_RESPUESTA = 4 * 1024 * 1024;

    interface Respuesta {
        void enviar(String id, int estado, String texto);
    }

    /** Abre una dirección en el navegador del sistema (el inicio de sesión no se hace dentro de la app). */
    interface Navegador {
        void abrir(String url);
    }

    private final SharedPreferences prefs;
    private final Respuesta respuesta;
    private final Navegador navegador;
    private final ExecutorService hilos = Executors.newFixedThreadPool(2);

    PuenteNativo(SharedPreferences prefs, Respuesta respuesta, Navegador navegador) {
        this.prefs = prefs;
        this.respuesta = respuesta;
        this.navegador = navegador;
    }

    /**
     * Abre el inicio de sesión del proveedor en el navegador del sistema. Solo se acepta una dirección https del
     * proveedor que la nube declaró; cualquier otra se rechaza.
     */
    @JavascriptInterface
    public boolean abrirNavegador(String url) {
        if (!Destinos.permitidoParaNavegador(url, prefs.getString(CLAVE_EMISOR, ""))) {
            return false;
        }
        navegador.abrir(url);
        return true;
    }

    /** Lo que devolvió el proveedor al volver a la app (por ejemplo ?code=...&state=...). Se entrega una sola vez. */
    @JavascriptInterface
    public String tomarVuelta() {
        String vuelta = prefs.getString(CLAVE_VUELTA, "");
        if (!vuelta.isEmpty()) {
            prefs.edit().remove(CLAVE_VUELTA).apply();
        }
        return vuelta;
    }

    /** Dirección base de la PC, por ejemplo http://192.168.1.50:8765. Vacía si aún no se configura. */
    @JavascriptInterface
    public String destino() {
        return prefs.getString(CLAVE_DESTINO, "");
    }

    /** Hace la petición en segundo plano; el resultado llega a capturaResultado(id, estado, texto). */
    @JavascriptInterface
    public void pedir(String id, String metodo, String url, String cuerpo, String cabeceras) {
        hilos.execute(() -> ejecutar(id, metodo, url, cuerpo, cabeceras));
    }

    private void ejecutar(String id, String metodo, String url, String cuerpo, String cabeceras) {
        String base = destino();
        boolean permitido = !base.isEmpty() && Destinos.permitido(url, base, prefs.getString(CLAVE_EMISOR, ""))
                && ("GET".equals(metodo) || "POST".equals(metodo));
        if (!permitido) {
            respuesta.enviar(id, 0, "");
            return;
        }
        boolean esConfig = url.equals(base + "/config.json");
        boolean esAppConfig = url.equals(base + "/app-config.json");
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setRequestMethod(metodo);
            c.setConnectTimeout(4000);
            c.setReadTimeout(8000);
            c.setUseCaches(false);

            JSONObject encabezados = new JSONObject(cabeceras == null || cabeceras.isEmpty() ? "{}" : cabeceras);
            Iterator<String> nombres = encabezados.keys();
            while (nombres.hasNext()) {
                String nombre = nombres.next();
                c.setRequestProperty(nombre, encabezados.getString(nombre));
            }

            if ("POST".equals(metodo)) {
                byte[] datos = (cuerpo == null ? "" : cuerpo).getBytes(StandardCharsets.UTF_8);
                c.setDoOutput(true);
                // El gestor de la PC exige Content-Length; sin esto Android enviaría el cuerpo en trozos.
                c.setFixedLengthStreamingMode(datos.length);
                try (OutputStream salida = c.getOutputStream()) {
                    salida.write(datos);
                }
            }

            int estado = c.getResponseCode();
            boolean correcto = estado >= 200 && estado < 300;
            String texto = leer(correcto ? c.getInputStream() : c.getErrorStream());
            c.disconnect();

            if (esConfig && correcto) {
                // Se guarda la última configuración buena para abrir la captura aunque la PC no responda.
                prefs.edit().putString(CLAVE_CONFIG, texto).apply();
            }
            if (esAppConfig) {
                // El proveedor de inicio de sesión lo declara el servidor que el usuario eligió; se recuerda para permitirle
                // ese único origen (https). Si el servidor no es una nube (el gestor responde 404), se olvida.
                guardarEmisor(correcto ? texto : null, estado);
            }
            respuesta.enviar(id, estado, texto);
        } catch (Exception e) {
            String cache = esConfig ? prefs.getString(CLAVE_CONFIG, null) : null;
            if (cache != null) {
                respuesta.enviar(id, 200, cache);
            } else {
                respuesta.enviar(id, 0, "");
            }
        }
    }

    private void guardarEmisor(String texto, int estado) {
        String origen = null;
        if (texto != null) {
            try {
                JSONObject oidc = new JSONObject(texto).optJSONObject("oidc");
                origen = oidc == null ? null : Destinos.origenEmisor(oidc.optString("emisor", ""));
            } catch (Exception e) {
                origen = null;
            }
        }
        if (origen != null) {
            prefs.edit().putString(CLAVE_EMISOR, origen).apply();
        } else if (estado == 404 || texto != null) {
            prefs.edit().remove(CLAVE_EMISOR).apply();
        }
    }

    private static String leer(InputStream entrada) throws IOException {
        if (entrada == null) {
            return "";
        }
        try (InputStream in = entrada) {
            ByteArrayOutputStream salida = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int n;
            while ((n = in.read(buffer)) != -1 && salida.size() + n <= LIMITE_RESPUESTA) {
                salida.write(buffer, 0, n);
            }
            return new String(salida.toByteArray(), StandardCharsets.UTF_8);
        }
    }
}
