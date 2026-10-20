package com.comfyuistudio.terminal.terminal

/** Tracks explicit host requests, independently of navigation inside the page. */
internal class WebViewNavigation {
    private var lastUrl: String? = null
    private var lastReload = -1

    fun shouldLoad(url: String, reload: Int): Boolean {
        if (url == lastUrl && reload == lastReload) return false
        lastUrl = url
        lastReload = reload
        return true
    }
}
