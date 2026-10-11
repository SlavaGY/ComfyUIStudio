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
        // НОВОЕ -- нужен для BuildConfig.DEBUG в TerminalActivity.kt
        // (см. её докстринг у setWebContentsDebuggingEnabled). AGP 8+
        // отключает генерацию BuildConfig по умолчанию, если явно не
        // запросить здесь -- без этой строки ссылка на BuildConfig.DEBUG
        // не скомпилируется вообще ("unresolved reference").
        buildConfig = true
    }

    // Kotlin 1.9.24 (не 2.0+) -- Compose включается версией расширения
    // компилятора здесь, а не отдельным плагином "kotlin.plugin.compose"
    // (см. комментарий в корневом build.gradle.kts). 1.5.14 -- версия
    // compose-compiler, совместимая именно с Kotlin 1.9.24, см.
    // https://developer.android.com/jetpack/androidx/releases/compose-kotlin
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }

    // НАЙДЕННЫЙ БАГ (живая сборка, §Этап 9): "jsch" (com.github.mwiede)
    // и его собственная транзитивная зависимость "jspecify" (аннотации
    // nullability) оба несут внутри себя одинаковый путь
    // META-INF/versions/9/OSGI-INF/MANIFEST.MF (multi-release JAR) --
    // Android не может решить, какой из двух класть в APK, и падает на
    // mergeDebugJavaResource. Файл -- это OSGi-метаданные, никак не
    // влияющие на код (jsch используется напрямую, не через OSGi) --
    // безопасно исключить целиком, как и подсказывает сама ошибка
    // gradle ("Adding a packaging block may help"). Заодно сразу
    // исключены самые типичные соседние конфликты (LICENSE/NOTICE в
    // META-INF, module-info.class для нескольких JPMS-модулей) --
    // именно они обычно всплывают ПО ОДНОМУ на каждый следующий прогон
    // сборки в проектах с несколькими сетевыми библиотеками
    // (httpx/zeroconf/jsch и т.п.), чтобы не тратить ещё несколько
    // циклов "пересобрать -- увидеть следующий дубликат".
    packaging {
        resources {
            // НАЙДЕННЫЙ БАГ №2 (живая сборка): тот же конфликт, что и с
            // jsch/jspecify выше, но со сдвинутой версией -- bcprov-jdk18on
            // несёт multi-release-вариант под /versions/11/ (а не /versions/9/,
            // как jspecify), поэтому точечный excludes выше его не ловит.
            // Меняем на wildcard по всем version-каталогам сразу, чтобы
            // следующая multi-release-зависимость не всплывала тем же way
            // ещё раз на очередной пересборке.
            excludes += "META-INF/versions/*/OSGI-INF/MANIFEST.MF"
            excludes += "META-INF/LICENSE*"
            excludes += "META-INF/NOTICE*"
            excludes += "META-INF/DEPENDENCIES"
            excludes += "META-INF/*.kotlin_module"
            pickFirsts += "module-info.class"
            // BouncyCastle (bcprov-jdk18on, см. зависимость ниже) --
            // хорошо известная на Android проблема: JAR-файл подписан
            // собственной подписью (META-INF/*.SF/*.RSA/*.DSA), а
            // Android-сборка эти файлы не умеет обрабатывать в
            // merge-шаге так же, как обычный JVM -- стандартный
            // конфликт для любого Android-проекта, добавляющего
            // bcprov напрямую (не через встроенный в Android
            // урезанный форк), исключаем заранее.
            excludes += "META-INF/*.SF"
            excludes += "META-INF/*.RSA"
            excludes += "META-INF/*.DSA"
        }
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

    // Второй, независимый способ доступа вне дома (см. дорожную карту,
    // §9, "вариант B через SSH") -- НЕ VpnService вообще, поэтому не
    // конфликтует с основным VPN пользователя (Hide.me), в отличие от
    // варианта A выше. com.github.mwiede:jsch -- активно поддерживаемый
    // форк JSch (оригинальный com.jcraft:jsch не обновлялся годами и не
    // поддерживает современные алгоритмы/форматы ключей) -- пакеты
    // остаются теми же com.jcraft.jsch.*, это полностью совместимая
    // замена, а не другой API.
    implementation("com.github.mwiede:jsch:2.28.0")
    // НАЙДЕННЫЙ БАГ (живая сборка, §Этап 9): "Auth cancel for methods
    // 'publickey,password,keyboard-interactive'" при подключении из
    // приложения, хотя тот же самый ключ прекрасно работает в Termux
    // (у него свой SSH-клиент, не через Java-провайдеры). Причина:
    // ed25519 ("ssh-ed25519") у mwiede/jsch требует либо Java 15+
    // (десктопный JDK с нативной поддержкой EdDSA), либо явно
    // добавленный в classpath BouncyCastle -- на обычном Android-
    // рантайме ни того, ни другого нет по умолчанию, поэтому JSch
    // тихо не может подписать хендшейк этим ключом и перебирает
    // методы аутентификации до полного отказа. BouncyCastle
    // регистрируется как security-провайдер в SshSocksProxy.kt перед
    // созданием сессии (Security.addProvider) -- версия ниже
    // последняя стабильная на Maven Central на момент написания.
    implementation("org.bouncycastle:bcprov-jdk18on:1.85.2")
    // ProxyController -- официальный API именно для подмены прокси
    // ТОЛЬКО для WebView этого процесса (см. её же докстринг в
    // TerminalActivity.kt про то, почему это не конфликтует с системным
    // VPN, в отличие от варианта A).
    implementation("androidx.webkit:webkit:1.16.0")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}

// НАЙДЕННЫЙ БАГ (живая сборка, §Этап 9): "androidx.webkit:webkit:1.16.0"
// (добавлен только что для ProxyController, см. зависимость выше)
// транзитивно тянет более новый kotlin-stdlib (2.1.20 -- метаданные
// версии 2.1.0), чем сам проект (Kotlin-плагин 1.9.24, см. корневой
// build.gradle.kts) -- компилятор 1.9.24 умеет читать метаданные не
// новее 2.0.0, отсюда "Module was compiled with an incompatible
// version of Kotlin" буквально на каждом файле проекта, включая уже
// давно рабочие. Явно закрепляем версию stdlib -- не даём ни одной
// зависимости протащить более новую транзитивно.
configurations.all {
    resolutionStrategy {
        force("org.jetbrains.kotlin:kotlin-stdlib:1.9.24")
        force("org.jetbrains.kotlin:kotlin-stdlib-common:1.9.24")
        force("org.jetbrains.kotlin:kotlin-stdlib-jdk7:1.9.24")
        force("org.jetbrains.kotlin:kotlin-stdlib-jdk8:1.9.24")
    }
}
