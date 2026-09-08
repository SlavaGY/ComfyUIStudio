// ComfyUI Studio Terminal — Android-приложение, этап 6 дорожной карты
// ComfyUIStudio_Remote_Roadmap.md ("Android-терминал, WebView-обёртка").
//
// Отдельный Gradle-проект, вне дерева comfyui_studio/ (Python) — по
// той же логике, что и весь Remote: не переиспользует код бэкенда,
// а является новым потребителем уже существующего Remote API (§0).

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "ComfyUIStudioTerminal"
include(":app")
