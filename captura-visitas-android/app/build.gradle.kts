plugins {
    id("com.android.application")
}

// La firma de publicación vive fuera de la carpeta del proyecto (no se sincroniza con OneDrive ni va a git).
// Archivo esperado: %USERPROFILE%\.android-keys\captura-visitas.properties
// con las claves storeFile, storePassword, keyAlias y keyPassword.
val archivoFirma = File(System.getProperty("user.home"), ".android-keys/captura-visitas.properties")

android {
    namespace = "com.nearustech.capturavisitas"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.nearustech.capturavisitas"
        minSdk = 24
        targetSdk = 35
        versionCode = 2
        versionName = "1.1.0"
    }

    signingConfigs {
        if (archivoFirma.exists()) {
            create("publicacion") {
                val p = java.util.Properties().apply { archivoFirma.inputStream().use { load(it) } }
                storeFile = file(p.getProperty("storeFile"))
                storePassword = p.getProperty("storePassword")
                keyAlias = p.getProperty("keyAlias")
                keyPassword = p.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (archivoFirma.exists()) {
                signingConfig = signingConfigs.getByName("publicacion")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// La APK usa la misma pantalla de captura que la PWA. Se copia al compilar para no mantener dos versiones.
// Solo se incluye index.html y el lector de QR que usa: ni taller, ni seguimiento, ni el gestor, ni config.json
// (la configuración se descarga de la PC o de la nube y queda en caché en el teléfono).
val copiarCapturaWeb by tasks.registering(Copy::class) {
    from(rootProject.file("../captura-visitas-pwa")) {
        include("index.html")
    }
    from(rootProject.file("../captura-visitas-pwa/vendor")) {
        include("qrcode.js")      // en la raíz de assets: igual que build-apk.ps1
    }
    into("src/main/assets")
}

tasks.named("preBuild") {
    dependsOn(copiarCapturaWeb)
}

// Sin dependencias externas: solo el SDK de Android.
dependencies {
}
