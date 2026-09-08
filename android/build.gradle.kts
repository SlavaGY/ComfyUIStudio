// См. settings.gradle.kts — единственная задача корневого файла здесь
// это подключить версии AGP/Kotlin один раз для всех модулей (сейчас
// модуль всего один — :app, но по конвенции держим версии на корне).
//
// ИСПРАВЛЕНО (после ошибки сборки в Android Studio): плагина
// "org.jetbrains.kotlin.plugin.compose" для Kotlin 1.9.24 не
// существует — этот отдельный Gradle-плагин (K2-компилятор Compose)
// появился только с Kotlin 2.0.0. Для 1.9.x Compose включается иначе —
// через `buildFeatures.compose = true` + `composeOptions.
// kotlinCompilerExtensionVersion` прямо в android { } модуля :app (см.
// app/build.gradle.kts), без отдельного плагина здесь.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
    // Push-уведомления, §Этап 6.5 дорожной карты. "apply false" здесь и
    // условное применение в app/build.gradle.kts (только если
    // google-services.json реально положен в android/app/) -- без
    // этого сборка ломалась бы для всех, кто ещё не завёл Firebase-
    // проект, хотя push -- опциональная функция (см. её же докстринг:
    // "отсутствие Firebase-проекта не должно ничего ломать", тот же
    // принцип и здесь, просто на уровне сборки, а не рантайма).
    id("com.google.gms.google-services") version "4.4.2" apply false
}
