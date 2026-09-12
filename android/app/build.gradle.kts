plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Push-уведомления, §Этап 6.5 дорожной карты -- применяем плагин
// google-services ТОЛЬКО если файл реально положен в android/app/
// (см. комментарий в корневом build.gradle.kts): без этой проверки
// сборка ломалась бы для всех, у кого ещё нет своего Firebase-проекта,
// хотя push -- опциональная функция и не должна быть условием сборки
// вообще. Положите свой google-services.json сюда, чтобы включить.
val hasGoogleServicesConfig = file("google-services.json").exists()
if (hasGoogleServicesConfig) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "com.comfyuistudio.terminal"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.comfyuistudio.terminal"
        minSdk = 26 // NsdManager.resolveService текущего API + EncryptedSharedPreferences
        targetSdk = 34
        versionCode = 1
        versionName = "0.6.5" // синхронизирован с этапом 6.5 дорожной карты, не с Studio-версией

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        // §Этап 9 (WireGuard-туннель) -- com.wireguard.android:tunnel
        // использует Java 8+ API (java.time и т.п.) напрямую, без
        // собственного desugaring -- обязательное требование самой
        // библиотеки (см. её README), не наша прихоть.
        isCoreLibraryDesugaringEnabled = true
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    // Kotlin 1.9.24 (не 2.0+) -- Compose включается версией расширения
    // компилятора здесь, а не отдельным плагином "kotlin.plugin.compose"
    // (см. комментарий в корневом build.gradle.kts). 1.5.14 -- версия
    // compose-compiler, совместимая именно с Kotlin 1.9.24, см.
    // https://developer.android.com/jetpack/androidx/releases/compose-kotlin
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.activity:activity-compose:1.9.1")
    // DiscoveryService.kt -- callbackFlow вокруг NsdManager
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // Единственный полностью нативный экран (Pairing) — см. §Этап 6
    // дорожной карты. WebView-терминал ниже намеренно НЕ на Compose:
    // AndroidView-обёртка над обычным android.webkit.WebView.
    val composeBom = platform("androidx.compose:compose-bom:2024.06.00")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui-tooling-preview")
    debugImplementation("androidx.compose.ui:ui-tooling")

    // TokenStore.kt — EncryptedSharedPreferences (§Этап 6)
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // Push-уведомления, §Этап 6.5 дорожной карты. firebase-messaging
    // работает и без google-services.json/применённого плагина (см.
    // условие выше) -- просто не сможет получить реальный FCM-токен
    // без него; весь код, который его запрашивает (PairingScreen.kt,
    // FcmService.kt), обёрнут в try/catch именно на этот случай (см.
    // их комментарии) -- собранное без Firebase приложение работает
    // как раньше, просто без push, а не падает.
    implementation(platform("com.google.firebase:firebase-bom:33.1.2"))
    implementation("com.google.firebase:firebase-messaging-ktx")

    // Клиент для /api/v1/remote/pair/confirm — простой JSON без Gson/
    // Moshi (одно тело запроса, одно поле ответа кроме device_id) —
    // не оправдывает ещё одну зависимость только ради сериализации,
    // по тому же принципу, каким на сервере обошлись без Jinja2 для
    // одной HTML-страницы (см. routes/home.py на сервере).
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // §Этап 9 дорожной карты -- доступ вне домашней сети, вариант A
    // (системный VpnService, см. ComfyUIStudio_Remote_Roadmap.md).
    // Именно tunnel-библиотека (не полное GUI-приложение WireGuard) --
    // то же встраиваемое ядро, что использует официальное приложение,
    // без его Activity/UI, см. https://github.com/WireGuard/wireguard-android.
    // Версия -- последняя на Maven Central на момент написания
    // (проверено веб-поиском, т.к. сетевой доступ из этой песочницы
    // недоступен для прямой проверки) -- стоит свериться с
    // https://search.maven.org/artifact/com.wireguard.android/tunnel
    // перед сборкой на случай более новой версии.
    implementation("com.wireguard.android:tunnel:1.0.20260102")
    // Обязательное требование библиотеки выше, см. compileOptions.
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.4")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
