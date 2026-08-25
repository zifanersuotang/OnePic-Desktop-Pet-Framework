package com.onepic.desktop.pet;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

/** 验证自定义模型服务地址不会绕过 HTTPS 与来源绑定边界。 */
public final class CompanionEndpointTest {
    @Test
    public void appendsChatCompletionsToV1Base() throws Exception {
        CompanionEndpoint endpoint = CompanionEndpoint.parse("https://API.OpenAI.com/v1/");

        assertEquals("https://api.openai.com/v1", endpoint.baseUrl());
        assertEquals("https://api.openai.com", endpoint.origin());
        assertEquals(
                "https://api.openai.com/v1/chat/completions",
                endpoint.chatCompletionsUrl().toString()
        );
    }

    @Test
    public void bareHttpsHostUsesV1Path() throws Exception {
        CompanionEndpoint endpoint = CompanionEndpoint.parse("https://example.com");

        assertEquals(
                "https://example.com/v1/chat/completions",
                endpoint.chatCompletionsUrl().toString()
        );
    }

    @Test
    public void preservesExplicitPortAndCompletedPath() throws Exception {
        CompanionEndpoint endpoint = CompanionEndpoint.parse(
                "https://example.com:8443/custom/chat/completions"
        );

        assertEquals("https://example.com:8443", endpoint.origin());
        assertEquals(
                "https://example.com:8443/custom/chat/completions",
                endpoint.chatCompletionsUrl().toString()
        );
    }

    @Test
    public void rejectsHttpCredentialsQueryAndRelativeSegments() {
        assertThrows(
                CompanionEndpoint.ValidationException.class,
                () -> CompanionEndpoint.parse("http://example.com/v1")
        );
        assertThrows(
                CompanionEndpoint.ValidationException.class,
                () -> CompanionEndpoint.parse("https://user:pass@example.com/v1")
        );
        assertThrows(
                CompanionEndpoint.ValidationException.class,
                () -> CompanionEndpoint.parse("https://example.com/v1?token=secret")
        );
        assertThrows(
                CompanionEndpoint.ValidationException.class,
                () -> CompanionEndpoint.parse("https://example.com/a/../v1")
        );
        assertThrows(
                CompanionEndpoint.ValidationException.class,
                () -> CompanionEndpoint.parse("https://example.com:0/v1")
        );
    }
}
