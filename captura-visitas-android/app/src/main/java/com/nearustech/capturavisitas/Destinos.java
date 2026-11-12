package com.nearustech.capturavisitas;

import java.net.MalformedURLException;
import java.net.URL;
import java.util.Locale;

/**
 * Reglas de a dónde puede hablar la app. Sin dependencias de Android, para poder probarlas con java a secas
 * (ver app/src/test).
 *
 * Hay dos servidores permitidos: el que guardó el usuario (la PC en la red local por http, o la nube por https) y, solo
 * en modo nube, el proveedor de inicio de sesión que ese servidor declara en /app-config.json. Se comparan siempre
 * origen contra origen (esquema, host y puerto ya interpretados), nunca con texto que "empiece igual".
 */
final class Destinos {

    private Destinos() {
    }

    /**
     * Convierte lo que escribe el usuario en la dirección base del servidor.
     * - PC en la red local: http://IP:puerto (el puerto es obligatorio).
     * - Nube: https://dominio (el puerto es opcional).
     * Devuelve null si no es una dirección válida.
     */
    static String normalizar(String entrada) {
        String texto = entrada == null ? "" : entrada.trim();
        if (texto.isEmpty()) {
            return null;
        }
        if (!texto.toLowerCase(Locale.ROOT).matches("^https?://.*")) {
            texto = "http://" + texto;
        }
        try {
            URL url = new URL(texto);
            String host = url.getHost();
            if (host == null || host.isEmpty() || url.getUserInfo() != null) {
                return null;
            }
            String esquema = url.getProtocol().toLowerCase(Locale.ROOT);
            host = host.toLowerCase(Locale.ROOT);
            int puerto = url.getPort();
            if ("https".equals(esquema)) {
                return puerto == -1 || puerto == 443 ? "https://" + host : "https://" + host + ":" + puerto;
            }
            return puerto == -1 ? null : "http://" + host + ":" + puerto;
        } catch (MalformedURLException e) {
            return null;
        }
    }

    /** esquema://host:puerto en minúsculas, con el puerto por omisión explícito. Null si no es una dirección con host. */
    static String origen(String direccion) {
        if (direccion == null || direccion.isEmpty()) {
            return null;
        }
        try {
            URL url = new URL(direccion);
            String host = url.getHost();
            if (host == null || host.isEmpty() || url.getUserInfo() != null) {
                return null;
            }
            String esquema = url.getProtocol().toLowerCase(Locale.ROOT);
            if (!"http".equals(esquema) && !"https".equals(esquema)) {
                return null;
            }
            int puerto = url.getPort() == -1 ? url.getDefaultPort() : url.getPort();
            return esquema + "://" + host.toLowerCase(Locale.ROOT) + ":" + puerto;
        } catch (MalformedURLException e) {
            return null;
        }
    }

    /**
     * El origen del proveedor de inicio de sesión que declara el servidor. Solo se acepta https: los tokens no viajan en
     * texto plano. Null si no hay proveedor o no es https.
     */
    static String origenEmisor(String emisor) {
        String origen = origen(emisor);
        return origen != null && origen.startsWith("https://") ? origen : null;
    }

    /** ¿Puede la página pedir esta dirección? Solo al servidor guardado o al proveedor que ese servidor declaró. */
    static boolean permitido(String direccion, String base, String origenEmisor) {
        String origen = origen(direccion);
        if (origen == null) {
            return false;
        }
        if (base != null && !base.isEmpty() && origen.equals(origen(base))) {
            return true;
        }
        return origenEmisor != null && !origenEmisor.isEmpty() && origen.equals(origenEmisor);
    }

    /** ¿Es una dirección que solo se puede abrir en el navegador del sistema para iniciar sesión? (https del proveedor). */
    static boolean permitidoParaNavegador(String direccion, String origenEmisor) {
        return direccion != null && direccion.toLowerCase(Locale.ROOT).startsWith("https://")
                && origenEmisor != null && origenEmisor.equals(origen(direccion));
    }
}
