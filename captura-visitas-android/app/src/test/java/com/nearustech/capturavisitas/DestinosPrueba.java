package com.nearustech.capturavisitas;

/**
 * Pruebas de las reglas de destinos, sin Android ni dependencias. Desde la carpeta captura-visitas-android:
 *
 *   javac -d build/prueba app/src/main/java/com/nearustech/capturavisitas/Destinos.java app/src/test/java/com/nearustech/capturavisitas/DestinosPrueba.java
 *   java -cp build/prueba com.nearustech.capturavisitas.DestinosPrueba
 *
 * Termina con código 1 si algo falla.
 */
public final class DestinosPrueba {

    private static int fallas = 0;
    private static int total = 0;

    private static void igual(String descripcion, Object esperado, Object obtenido) {
        total++;
        boolean ok = esperado == null ? obtenido == null : esperado.equals(obtenido);
        if (!ok) {
            fallas++;
            System.out.println("FALLA  " + descripcion + "\n         esperado: " + esperado + "\n         obtenido: " + obtenido);
        }
    }

    public static void main(String[] args) {
        // --- normalizar: lo que escribe el usuario ---
        igual("PC con puerto", "http://192.168.1.50:8765", Destinos.normalizar("192.168.1.50:8765"));
        igual("PC con la dirección completa del gestor", "http://192.168.1.50:8765", Destinos.normalizar("http://192.168.1.50:8765/index.html"));
        igual("PC sin puerto no vale", null, Destinos.normalizar("192.168.1.50"));
        igual("PC por http sin puerto no vale", null, Destinos.normalizar("http://captura.miempresa.com"));
        igual("nube https sin puerto", "https://captura.miempresa.com", Destinos.normalizar("https://captura.miempresa.com"));
        igual("nube https con ruta y espacios", "https://captura.miempresa.com", Destinos.normalizar("  https://Captura.MiEmpresa.com/algo/mas?x=1 "));
        igual("nube https con el puerto por omisión", "https://captura.miempresa.com", Destinos.normalizar("https://captura.miempresa.com:443"));
        igual("nube https con otro puerto", "https://captura.miempresa.com:8443", Destinos.normalizar("https://captura.miempresa.com:8443/"));
        igual("vacío", null, Destinos.normalizar("   "));
        igual("nulo", null, Destinos.normalizar(null));
        igual("con usuario y contraseña no vale", null, Destinos.normalizar("https://usuario:clave@captura.miempresa.com"));
        igual("esquema raro", "http://ftp.miempresa.com:21", Destinos.normalizar("ftp.miempresa.com:21"));
        igual("sin host", null, Destinos.normalizar("https://"));

        // --- origen: esquema, host y puerto interpretados ---
        igual("origen https por omisión", "https://a.com:443", Destinos.origen("https://A.com/x"));
        igual("origen http con puerto", "http://10.0.0.2:8765", Destinos.origen("http://10.0.0.2:8765/api"));
        igual("origen de esquema propio", null, Destinos.origen("com.nearustech.capturavisitas:/oauth"));
        igual("origen con usuario@", null, Destinos.origen("https://a.com@evil.com/"));
        igual("origen vacío", null, Destinos.origen(""));

        // --- emisor: solo https ---
        igual("emisor https", "https://login.miempresa.com:443", Destinos.origenEmisor("https://login.miempresa.com/tenant/v2.0"));
        igual("emisor http no se acepta", null, Destinos.origenEmisor("http://login.miempresa.com"));
        igual("sin emisor", null, Destinos.origenEmisor(""));

        // --- permitido: la página solo habla con el servidor guardado y con el proveedor que ese servidor declaró ---
        String nube = "https://captura.miempresa.com";
        String emisor = "https://login.miempresa.com:443";
        igual("a la nube", true, Destinos.permitido(nube + "/api/visitas", nube, emisor));
        igual("a la nube con el puerto explícito", true, Destinos.permitido(nube + ":443/api/visitas", nube, emisor));
        igual("al proveedor declarado", true, Destinos.permitido("https://login.miempresa.com/tenant/oauth2/v2.0/token", nube, emisor));
        igual("a otro sitio no", false, Destinos.permitido("https://evil.example/robar", nube, emisor));
        igual("a un dominio que solo se parece (prefijo) no", false, Destinos.permitido("https://captura.miempresa.com.evil.example/api", nube, emisor));
        igual("al servidor con usuario@ que apunta a otro host no", false, Destinos.permitido("https://captura.miempresa.com@evil.example/api", nube, emisor));
        igual("el proveedor por http no", false, Destinos.permitido("http://login.miempresa.com/token", nube, emisor));
        igual("sin proveedor declarado, solo el servidor", false, Destinos.permitido("https://login.miempresa.com/token", nube, ""));
        igual("sin servidor guardado nada pasa", false, Destinos.permitido("https://captura.miempresa.com/api", "", emisor));
        igual("PC en la red local", true, Destinos.permitido("http://192.168.1.50:8765/api/visitas", "http://192.168.1.50:8765", ""));
        igual("PC: otro puerto no", false, Destinos.permitido("http://192.168.1.50:9999/api/visitas", "http://192.168.1.50:8765", ""));
        igual("dirección nula", false, Destinos.permitido(null, nube, emisor));

        // --- navegador del sistema: solo https del proveedor declarado ---
        igual("inicio de sesión del proveedor", true, Destinos.permitidoParaNavegador("https://login.miempresa.com/authorize?x=1", emisor));
        igual("la nube no se abre como inicio de sesión", false, Destinos.permitidoParaNavegador("https://captura.miempresa.com/", emisor));
        igual("intento de abrir otra app", false, Destinos.permitidoParaNavegador("intent://evil#Intent;end", emisor));
        igual("sin proveedor declarado", false, Destinos.permitidoParaNavegador("https://login.miempresa.com/authorize", ""));
        igual("http no", false, Destinos.permitidoParaNavegador("http://login.miempresa.com/authorize", emisor));

        System.out.println((total - fallas) + " de " + total + " comprobaciones correctas" + (fallas == 0 ? "" : " — HAY FALLAS"));
        if (fallas > 0) {
            System.exit(1);
        }
    }
}
