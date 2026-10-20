package com.comfyuistudio.terminal.terminal

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class WebViewNavigationTest {
    @Test
    fun stateUpdatesDoNotSendTheWebViewBackHome() {
        val navigation = WebViewNavigation()
        assertTrue(navigation.shouldLoad("http://studio/?token=test", 0))
        // Page callbacks, keyboard changes and app-panel visibility may all
        // recompose the host after the user has opened /apps/lm_studio/.
        repeat(10) { assertFalse(navigation.shouldLoad("http://studio/?token=test", 0)) }
    }

    @Test
    fun explicitRetryAndNewPushPathStillLoad() {
        val navigation = WebViewNavigation()
        assertTrue(navigation.shouldLoad("http://studio/", 0))
        assertTrue(navigation.shouldLoad("http://studio/", 1))
        assertFalse(navigation.shouldLoad("http://studio/", 1))
        assertTrue(navigation.shouldLoad("http://studio/apps/lm_studio/", 1))
    }

    @Test
    fun recreatedWebViewLoadsEvenWhenTheAddressHasNotChanged() {
        assertTrue(WebViewNavigation().shouldLoad("http://studio/", 0))
        assertTrue(WebViewNavigation().shouldLoad("http://studio/", 0))
    }
}
