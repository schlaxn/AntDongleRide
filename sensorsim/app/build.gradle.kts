import java.io.File
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Same signing scheme as android/: details come from a properties file OUTSIDE the repo, whose path is in
// ANTLOGGER_SIGNING_PROPS. Without it the generic debug key is used.
val keystoreProps = Properties().apply {
    val f = System.getenv("ANTLOGGER_SIGNING_PROPS")?.let { File(it) } ?: rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "app.sensorsim"
    compileSdk = 36

    signingConfigs {
        if (keystoreProps.getProperty("storeFile") != null) {
            create("release") {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release { signingConfigs.findByName("release")?.let { signingConfig = it } }
        debug { signingConfigs.findByName("release")?.let { signingConfig = it } }
    }

    defaultConfig {
        applicationId = "app.sensorsim"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1-phase0"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    testImplementation("junit:junit:4.13.2")
}
