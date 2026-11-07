import java.io.File
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing details (storeFile, storePassword, keyAlias, keyPassword) come from a properties file kept
// OUTSIDE the repo, e.g. E:/jschlager/keys/signing.properties. Its path is given in the environment variable
// ANTLOGGER_SIGNING_PROPS; android/keystore.properties (git-ignored) is the fallback. Without any such file
// a release build is simply unsigned; nothing secret lives in the repo.
val keystoreProps = Properties().apply {
    val f = System.getenv("ANTLOGGER_SIGNING_PROPS")?.let { File(it) } ?: rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "app.antlogger"
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
        release {
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
        // Debug builds use the SAME key as release when the signing properties are available, so debug and
        // release (same applicationId) can be swapped with `adb install -r` without uninstalling. Without the
        // properties file the standard Android debug key is used. Never hand out a debug APK: it is debuggable.
        debug {
            signingConfigs.findByName("release")?.let { signingConfig = it }
        }
    }

    defaultConfig {
        applicationId = "app.antlogger"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.1-spike"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("com.garmin:fit:21.176.0")
    testImplementation("junit:junit:4.13.2")
}
