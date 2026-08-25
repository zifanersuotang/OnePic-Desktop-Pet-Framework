package com.onepic.desktop.pet;

import java.net.URI;
import java.net.URISyntaxException;
import java.net.URL;
import java.util.Locale;
import java.util.Objects;

/**
 * 校验并规范化 Android 智能陪伴使用的 OpenAI-compatible HTTPS 端点。
 *
 * <p>端点禁止用户名、密码、查询参数、片段和明文 HTTP，避免 API Key 被嵌入 URL、
 * 经重定向泄露或被意外发送给非 TLS 服务。用户只需填写服务根地址或 v1 地址。</p>
 */
final class CompanionEndpoint {
    static final int MAX_BASE_URL_LENGTH = 512;

    private final URI baseUri;
    private final URI chatCompletionsUri;
    private final String origin;

    private CompanionEndpoint(URI baseUri, URI chatCompletionsUri, String origin) {
        this.baseUri = baseUri;
        this.chatCompletionsUri = chatCompletionsUri;
        this.origin = origin;
    }

    static CompanionEndpoint parse(String rawValue) throws ValidationException {
        String value = rawValue == null ? "" : rawValue.trim();
        if (value.isEmpty()) {
            throw new ValidationException("服务地址不能为空");
        }
        if (value.length() > MAX_BASE_URL_LENGTH) {
            throw new ValidationException("服务地址过长");
        }

        final URI parsed;
        try {
            parsed = new URI(value);
        } catch (URISyntaxException error) {
            throw new ValidationException("服务地址格式无效");
        }
        if (!"https".equalsIgnoreCase(parsed.getScheme())) {
            throw new ValidationException("首版仅允许 HTTPS 服务地址");
        }
        if (parsed.getHost() == null || parsed.getHost().trim().isEmpty()) {
            throw new ValidationException("服务地址缺少有效主机名");
        }
        if (parsed.getRawUserInfo() != null) {
            throw new ValidationException("服务地址不能包含用户名或密码");
        }
        if (parsed.getRawQuery() != null || parsed.getRawFragment() != null) {
            throw new ValidationException("服务地址不能包含查询参数或片段");
        }
        if (parsed.getPort() == 0 || parsed.getPort() > 65535) {
            throw new ValidationException("服务地址端口无效");
        }
        URI normalized = parsed.normalize();
        if (!Objects.equals(normalized.getRawPath(), parsed.getRawPath())) {
            throw new ValidationException("服务地址不能包含相对路径段");
        }

        String path = parsed.getPath();
        if (path == null || path.isEmpty() || "/".equals(path)) {
            path = "/v1";
        }
        while (path.length() > 1 && path.endsWith("/")) {
            path = path.substring(0, path.length() - 1);
        }
        String chatPath = path.endsWith("/chat/completions")
                ? path
                : path + "/chat/completions";

        String scheme = parsed.getScheme().toLowerCase(Locale.ROOT);
        String host = parsed.getHost().toLowerCase(Locale.ROOT);
        try {
            URI cleanBase = new URI(
                    scheme,
                    null,
                    host,
                    parsed.getPort(),
                    path,
                    null,
                    null
            );
            URI chat = new URI(
                    scheme,
                    null,
                    host,
                    parsed.getPort(),
                    chatPath,
                    null,
                    null
            );
            URI originUri = new URI(
                    scheme,
                    null,
                    host,
                    parsed.getPort(),
                    null,
                    null,
                    null
            );
            return new CompanionEndpoint(cleanBase, chat, originUri.toString());
        } catch (URISyntaxException error) {
            throw new ValidationException("服务地址无法规范化");
        }
    }

    String baseUrl() {
        return baseUri.toString();
    }

    String origin() {
        return origin;
    }

    URL chatCompletionsUrl() throws ValidationException {
        try {
            return chatCompletionsUri.toURL();
        } catch (Exception error) {
            throw new ValidationException("聊天端点无法打开");
        }
    }

    static final class ValidationException extends Exception {
        ValidationException(String message) {
            super(message);
        }
    }
}
