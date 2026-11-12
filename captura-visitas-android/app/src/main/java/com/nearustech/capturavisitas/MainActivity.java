package com.nearustech.capturavisitas;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Menu;
import android.view.MenuItem;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;
import android.Manifest;
import android.content.pm.PackageManager;
import android.webkit.GeolocationPermissions;
import android.webkit.WebChromeClient;

import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.IOException;

/**
 * Solo captura: muestra la pantalla de captura de visita (la misma de la PWA). El resto de módulos
 * (taller, seguimiento y el gestor de escritorio) no forman parte de esta app.
 */
public class MainActivity extends Activity {

    // Dominio ficticio que sirve los archivos de la propia APK. Así la página tiene origen https, como en el navegador.
    private static final String HOST = "appassets.androidplatform.net";
    private static final String INICIO = "https://" + HOST + "/index.html";
    private static final String ARCHIVO_CAPTURA = "index.html";
    // Lo único que la APK sirve como propio: la pantalla y el lector de QR que esa pantalla usa.
    private static final String RUTA_QR = "/vendor/qrcode.js";
    private static final String ARCHIVO_QR = "qrcode.js";       // en assets/ (la página lo pide como /vendor/qrcode.js)
    // A donde el proveedor de inicio de sesión devuelve al usuario (navegador del sistema -> app).
    static final String ESQUEMA_RETORNO = "com.nearustech.capturavisitas";
    static final String RUTA_RETORNO = "/oauth";
    private static final int MENU_DIRECCION = 1;
    private static final int PERMISO_UBICACION = 7;

    private GeolocationPermissions.Callback callbackUbicacion;
    private String origenUbicacion;

    private WebView webView;
    private SharedPreferences prefs;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences(PuenteNativo.NOMBRE_PREFS, MODE_PRIVATE);
        guardarVuelta(getIntent());     // la app pudo cerrarse mientras el usuario iniciaba sesión en el navegador

        webView = new WebView(this);
        setContentView(webView);

        WebSettings ajustes = webView.getSettings();
        ajustes.setJavaScriptEnabled(true);
        ajustes.setDomStorageEnabled(true);
        ajustes.setAllowFileAccess(false);
        ajustes.setAllowContentAccess(false);
        // Las fotos de las casas vienen de la PC por http, dentro de la red local: la página es https.
        ajustes.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.addJavascriptInterface(new PuenteNativo(prefs, this::responder, this::abrirEnNavegador), "CapturaAndroid");

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (!HOST.equals(uri.getHost())) {
                    return null; // Fuentes de Google y demás: la WebView los pide por red.
                }
                String ruta = uri.getPath();
                try {
                    if ("/index.html".equals(ruta)) {
                        return new WebResourceResponse("text/html", "utf-8", getAssets().open(ARCHIVO_CAPTURA));
                    }
                    if (RUTA_QR.equals(ruta)) {
                        return new WebResourceResponse("application/javascript", "utf-8", getAssets().open(ARCHIVO_QR));
                    }
                } catch (IOException e) {
                    return null;
                }
                return new WebResourceResponse("text/plain", "utf-8", 404, "No encontrado",
                        null, new ByteArrayInputStream(new byte[0]));
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (HOST.equals(uri.getHost())) {
                    return false;
                }
                // Cualquier enlace externo se abre fuera de la app, nunca dentro de la captura.
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
                return true;
            }
        });

        // La ubicación del asesor solo se usa con una cita en curso; la página la pide al iniciar la cita.
        ajustes.setGeolocationEnabled(true);
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onGeolocationPermissionsShowPrompt(String origen, GeolocationPermissions.Callback callback) {
                if (tieneUbicacion()) {
                    callback.invoke(origen, true, false);
                    return;
                }
                origenUbicacion = origen;
                callbackUbicacion = callback;
                requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                        Manifest.permission.ACCESS_COARSE_LOCATION}, PERMISO_UBICACION);
            }
        });

        webView.loadUrl(INICIO);

        if (prefs.getString(PuenteNativo.CLAVE_DESTINO, "").isEmpty()) {
            mostrarDireccion();
        }
    }

    /** El proveedor de inicio de sesión devolvió al usuario a la app (singleTask: llega aquí, no abre otra ventana). */
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        if (guardarVuelta(intent)) {
            webView.evaluateJavascript("if (typeof window.capturaVuelta === 'function') window.capturaVuelta();", null);
        }
    }

    /**
     * Si el intento trae la vuelta del inicio de sesión (com.nearustech.capturavisitas:/oauth?code=...&state=...), la guarda
     * para que la página la tome. No se confía en ella: la página la valida contra el state y el verificador PKCE que
     * guardó antes de salir, así que una app ajena que dispare este enlace no inicia sesión.
     */
    private boolean guardarVuelta(Intent intent) {
        Uri datos = intent == null ? null : intent.getData();
        if (datos == null || !ESQUEMA_RETORNO.equals(datos.getScheme()) || !RUTA_RETORNO.equals(datos.getPath())) {
            return false;
        }
        String consulta = datos.getEncodedQuery();
        prefs.edit().putString(PuenteNativo.CLAVE_VUELTA, "?" + (consulta == null ? "" : consulta)).apply();
        return true;
    }

    private void abrirEnNavegador(String url) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
        } catch (RuntimeException e) {
            runOnUiThread(() -> Toast.makeText(this, "No hay un navegador para iniciar sesión.", Toast.LENGTH_LONG).show());
        }
    }

    /** Devuelve al JavaScript el resultado de una petición hecha por PuenteNativo. */
    private void responder(String id, int estado, String texto) {
        String js = "window.capturaResultado(" + JSONObject.quote(id) + "," + estado + ","
                + JSONObject.quote(texto) + ");";
        runOnUiThread(() -> webView.evaluateJavascript(js, null));
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add(0, MENU_DIRECCION, 0, "Dirección de la PC o la nube");
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if (item.getItemId() == MENU_DIRECCION) {
            mostrarDireccion();
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    private void mostrarDireccion() {
        EditText campo = new EditText(this);
        campo.setSingleLine(true);
        campo.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        campo.setHint("192.168.1.50:8765  o  https://captura.tuempresa.com");
        campo.setText(prefs.getString(PuenteNativo.CLAVE_DESTINO, ""));
        int margen = (int) (16 * getResources().getDisplayMetrics().density);
        campo.setPadding(margen, margen, margen, margen);

        new AlertDialog.Builder(this)
                .setTitle("Dirección de la PC o la nube")
                .setMessage("Con la PC: pega la dirección que muestra el gestor (pestaña Conexión); debe incluir el puerto. "
                        + "Con la nube: la dirección https que te dio tu administrador.")
                .setView(campo)
                .setPositiveButton("Guardar", (dialogo, cual) -> guardarDireccion(campo.getText().toString()))
                .setNegativeButton("Cancelar", null)
                .show();
    }

    private void guardarDireccion(String entrada) {
        String destino = Destinos.normalizar(entrada);
        if (destino == null) {
            Toast.makeText(this, "Dirección no válida. Ejemplos: 192.168.1.50:8765 o https://captura.tuempresa.com", Toast.LENGTH_LONG).show();
            return;
        }
        boolean cambio = !destino.equals(prefs.getString(PuenteNativo.CLAVE_DESTINO, ""));
        SharedPreferences.Editor cambios = prefs.edit().putString(PuenteNativo.CLAVE_DESTINO, destino);
        if (cambio) {
            // Otro servidor: el proveedor de inicio de sesión que se conocía ya no vale y la página debe decidir de nuevo
            // si trabaja con una PC o con la nube.
            cambios.remove(PuenteNativo.CLAVE_EMISOR).remove(PuenteNativo.CLAVE_VUELTA);
        }
        cambios.apply();
        if (cambio) {
            webView.reload();
        } else {
            // Misma dirección: sin recargar (no se pierde lo que se captura) se descarga la configuración y se envían pendientes.
            webView.evaluateJavascript(
                    "if (typeof refrescarConfig === 'function') refrescarConfig();"
                            + "if (typeof sincronizar === 'function') sincronizar(false);", null);
        }
        Toast.makeText(this, "Dirección guardada", Toast.LENGTH_SHORT).show();
    }

    private boolean tieneUbicacion() {
        return checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    @Override
    public void onRequestPermissionsResult(int codigo, String[] permisos, int[] resultados) {
        super.onRequestPermissionsResult(codigo, permisos, resultados);
        if (codigo == PERMISO_UBICACION && callbackUbicacion != null) {
            callbackUbicacion.invoke(origenUbicacion, tieneUbicacion(), false);
            callbackUbicacion = null;
            origenUbicacion = null;
        }
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
