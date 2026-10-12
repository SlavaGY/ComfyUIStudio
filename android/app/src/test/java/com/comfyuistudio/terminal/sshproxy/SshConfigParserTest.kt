package com.comfyuistudio.terminal.sshproxy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SshConfigParserTest {

    private val key = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"

    private fun body(
        available: Boolean = true,
        keyVersion: String = "3",
        host: String = "\"2001:db8::5\"",
        port: String = "2222",
        username: String = "\"comfyui-ssh\"",
        privateKey: String = "\"" + key.replace("\n", "\\n") + "\"",
    ): String =
        "{\"available\":$available,\"key_version\":$keyVersion,\"username\":$username," +
            "\"host\":$host,\"ssh_port\":$port,\"private_key\":$privateKey}"

    @Test
    fun parsesAvailableProfile() {
        val result = SshConfigParser.parse(body())

        assertTrue(result is SshConfigResponse.Available)
        result as SshConfigResponse.Available
        assertEquals(3, result.keyVersion)
        assertEquals("2001:db8::5", result.profile.host)
        assertEquals(2222, result.profile.port)
        assertEquals("comfyui-ssh", result.profile.username)
        assertEquals(key, result.profile.privateKeyPem)
        assertNull(result.profile.passphrase)
    }

    @Test
    fun unavailableWhenServerHasNoProfile() {
        val json = "{\"available\":false,\"key_version\":null,\"username\":null," +
            "\"host\":null,\"ssh_port\":null,\"private_key\":null}"

        assertEquals(SshConfigResponse.Unavailable, SshConfigParser.parse(json))
    }

    @Test
    fun normalizesWindowsLineEndingsInKey() {
        val crlfKey = "\"-----BEGIN OPENSSH PRIVATE KEY-----\\r\\nAAAA\\r\\n-----END OPENSSH PRIVATE KEY-----\\r\\n\""

        val result = SshConfigParser.parse(body(privateKey = crlfKey)) as SshConfigResponse.Available

        assertFalse(result.profile.privateKeyPem.contains("\r"))
        assertEquals(key, result.profile.privateKeyPem)
    }

    @Test
    fun trimsHostAndUsername() {
        val result = SshConfigParser.parse(
            body(host = "\" example.org \"", username = "\" user \""),
        ) as SshConfigResponse.Available

        assertEquals("example.org", result.profile.host)
        assertEquals("user", result.profile.username)
    }

    @Test
    fun invalidWhenNotJson() {
        assertTrue(SshConfigParser.parse("<html>502</html>") is SshConfigResponse.Invalid)
    }

    @Test
    fun invalidWhenKeyVersionMissingOrZero() {
        assertTrue(SshConfigParser.parse(body(keyVersion = "0")) is SshConfigResponse.Invalid)
        assertTrue(SshConfigParser.parse(body(keyVersion = "null")) is SshConfigResponse.Invalid)
    }

    @Test
    fun invalidWhenHostIsNullOrBlank() {
        assertTrue(SshConfigParser.parse(body(host = "null")) is SshConfigResponse.Invalid)
        assertTrue(SshConfigParser.parse(body(host = "\"  \"")) is SshConfigResponse.Invalid)
    }

    @Test
    fun invalidWhenPortOutOfRange() {
        assertTrue(SshConfigParser.parse(body(port = "0")) is SshConfigResponse.Invalid)
        assertTrue(SshConfigParser.parse(body(port = "70000")) is SshConfigResponse.Invalid)
    }

    @Test
    fun invalidWhenKeyIsNotAPrivateKey() {
        val result = SshConfigParser.parse(body(privateKey = "\"garbage\""))

        assertTrue(result is SshConfigResponse.Invalid)
    }

    @Test
    fun appliesOnlyWhenVersionDiffers() {
        assertTrue(SshConfigParser.shouldApply(serverVersion = 2, storedVersion = 0))
        assertTrue(SshConfigParser.shouldApply(serverVersion = 5, storedVersion = 4))
        assertFalse(SshConfigParser.shouldApply(serverVersion = 4, storedVersion = 4))
        assertTrue(SshConfigParser.shouldApply(serverVersion = 1, storedVersion = 4))
    }
}
