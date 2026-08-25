package com.onepic.desktop.pet;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.List;

import javax.net.ssl.HttpsURLConnection;

/**
 * 执行一次可取消的 OpenAI-compatible JSON 聊天请求。
 *
 * <p>客户端只接受 {@link CompanionEndpoint} 产生的 HTTPS URL，关闭自动重定向、
 * Cookie 与缓存，不记录请求或密钥，并限制输入、响应和错误文本长度。首版使用
 * 非流式 JSON，后续可在不改变存储与权限边界的情况下增加 SSE。</p>
 */
final class OpenAiCompatibleClient {
    private static final int CONNECT_TIMEOUT_MS = 15000;
    private static final int READ_TIMEOUT_MS = 70000;
    private static final int MAX_RESPONSE_BYTES = 1024 * 1024;
    private static final int MAX_VISIBLE_RESPONSE = 12000;
    private static final int MAX_HISTORY_CHARACTERS = 24000;

    Call newCall(Request request) {
        return new Call(request);
    }

    static final class Request {
        final CompanionEndpoint endpoint;
        final String apiKey;
        final String model;
        final String systemPrompt;
        final List<CompanionStore.Message> history;
        final String userMessage;
        final List<String> allowedActions;

        Request(
                CompanionEndpoint endpoint,
                String apiKey,
                String model,
                String systemPrompt,
                List<CompanionStore.Message> history,
                String userMessage,
                Collection<String> allowedActions
        ) {
            this.endpoint = endpoint;
            this.apiKey = apiKey == null ? "" : apiKey.trim();
            this.model = model == null ? "" : model.trim();
            this.systemPrompt = systemPrompt == null ? "" : systemPrompt.trim();
            this.history = history == null
                    ? Collections.emptyList()
                    : Collections.unmodifiableList(new ArrayList<>(history));
            this.userMessage = userMessage == null ? "" : userMessage.trim();
            this.allowedActions = allowedActions == null
                    ? Collections.emptyList()
                    : Collections.unmodifiableList(new ArrayList<>(allowedActions));
        }
    }

    static final class Result {
        final String text;
        final String action;

        Result(String text, String action) {
            this.text = text;
            this.action = action;
        }
    }

    static final class Call {
        private final Request request;
        private volatile HttpsURLConnection connection;
        private volatile boolean cancelled;

        Call(Request request) {
            this.request = request;
        }

        Result execute() throws ClientException {
            validateRequest();
            HttpsURLConnection active = null;
            try {
                byte[] body = requestBody().toString().getBytes(StandardCharsets.UTF_8);
                active = (HttpsURLConnection) request.endpoint
                        .chatCompletionsUrl()
                        .openConnection();
                connection = active;
                active.setRequestMethod("POST");
                active.setConnectTimeout(CONNECT_TIMEOUT_MS);
                active.setReadTimeout(READ_TIMEOUT_MS);
                active.setInstanceFollowRedirects(false);
                active.setUseCaches(false);
                active.setDoInput(true);
                active.setDoOutput(true);
                active.setFixedLengthStreamingMode(body.length);
                active.setRequestProperty("Accept", "application/json");
                active.setRequestProperty("Accept-Encoding", "identity");
                active.setRequestProperty("Cache-Control", "no-store");
                active.setRequestProperty("Pragma", "no-cache");
                active.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                active.setRequestProperty("Authorization", "Bearer " + request.apiKey);

                throwIfCancelled();
                try (OutputStream output = active.getOutputStream()) {
                    output.write(body);
                    output.flush();
                }
                throwIfCancelled();

                int status = active.getResponseCode();
                if (status >= 300 && status < 400) {
                    throw new ClientException("服务返回重定向，已按安全策略拒绝");
                }
                InputStream stream = status >= 200 && status < 300
                        ? active.getInputStream()
                        : active.getErrorStream();
                String response = stream == null ? "" : readBody(stream);
                throwIfCancelled();
                if (status < 200 || status >= 300) {
                    throw new ClientException(httpError(status, response));
                }
                return parseResponse(response);
            } catch (CompanionEndpoint.ValidationException error) {
                throw new ClientException(error.getMessage());
            } catch (ClientException error) {
                throw error;
            } catch (IOException error) {
                if (cancelled || Thread.currentThread().isInterrupted()) {
                    throw new ClientException("请求已取消");
                }
                throw new ClientException("网络请求失败，请检查服务地址与网络连接");
            } finally {
                connection = null;
                if (active != null) {
                    active.disconnect();
                }
            }
        }

        void cancel() {
            cancelled = true;
            HttpsURLConnection active = connection;
            if (active != null) {
                active.disconnect();
            }
        }

        private void validateRequest() throws ClientException {
            if (request.endpoint == null) {
                throw new ClientException("服务地址尚未配置");
            }
            if (request.apiKey.isEmpty()) {
                throw new ClientException("尚未安全保存 API Key");
            }
            if (request.model.isEmpty() || request.model.length() > 160) {
                throw new ClientException("模型名称为空或过长");
            }
            if (request.userMessage.isEmpty() || request.userMessage.length() > 4000) {
                throw new ClientException("消息不能为空且不能超过 4000 字");
            }
            if (request.systemPrompt.length() > 8000) {
                throw new ClientException("人设与记忆内容过长");
            }
        }

        private JSONObject requestBody() throws ClientException {
            try {
                JSONObject body = new JSONObject();
                body.put("model", request.model);
                body.put("stream", false);
                JSONArray messages = new JSONArray();
                if (!request.systemPrompt.isEmpty()) {
                    messages.put(message("system", request.systemPrompt));
                }
                for (CompanionStore.Message value : boundedHistory()) {
                    messages.put(message(value.role, value.content));
                }
                messages.put(message("user", request.userMessage));
                body.put("messages", messages);
                return body;
            } catch (JSONException error) {
                throw new ClientException("无法生成聊天请求");
            }
        }

        private static JSONObject message(String role, String content) throws JSONException {
            JSONObject value = new JSONObject();
            value.put("role", role);
            value.put("content", content);
            return value;
        }

        private List<CompanionStore.Message> boundedHistory() {
            List<CompanionStore.Message> reverse = new ArrayList<>();
            int totalCharacters = 0;
            for (int index = request.history.size() - 1; index >= 0; index--) {
                CompanionStore.Message value = request.history.get(index);
                if (!("user".equals(value.role) || "assistant".equals(value.role))
                        || value.content.isEmpty()
                        || value.content.length() > MAX_VISIBLE_RESPONSE) {
                    continue;
                }
                if (totalCharacters + value.content.length() > MAX_HISTORY_CHARACTERS) {
                    break;
                }
                reverse.add(value);
                totalCharacters += value.content.length();
            }
            Collections.reverse(reverse);
            return reverse;
        }

        private String readBody(InputStream stream) throws IOException, ClientException {
            try (InputStream input = stream; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[8192];
                int total = 0;
                int read;
                while ((read = input.read(buffer)) != -1) {
                    if (cancelled || Thread.currentThread().isInterrupted()) {
                        throw new ClientException("请求已取消");
                    }
                    total += read;
                    if (total > MAX_RESPONSE_BYTES) {
                        throw new ClientException("服务响应过大，已停止读取");
                    }
                    output.write(buffer, 0, read);
                }
                return output.toString(StandardCharsets.UTF_8.name());
            }
        }

        private Result parseResponse(String response) throws ClientException {
            try {
                JSONObject root = new JSONObject(response);
                JSONArray choices = root.optJSONArray("choices");
                if (choices == null || choices.length() == 0) {
                    throw new ClientException("服务响应不包含有效回复");
                }
                JSONObject message = choices.getJSONObject(0).optJSONObject("message");
                if (message == null) {
                    throw new ClientException("服务响应缺少 message");
                }
                String rawText = contentText(message.opt("content"));
                if (rawText.length() > MAX_VISIBLE_RESPONSE) {
                    rawText = rawText.substring(0, MAX_VISIBLE_RESPONSE);
                }
                CompanionReplyParser.ParsedReply parsed = CompanionReplyParser.parse(
                        rawText,
                        request.allowedActions
                );
                if (parsed.text.isEmpty()) {
                    throw new ClientException("模型返回了空回复");
                }
                return new Result(parsed.text, parsed.action);
            } catch (JSONException error) {
                throw new ClientException("服务返回了无法解析的 JSON");
            }
        }

        private static String contentText(Object content) throws JSONException {
            if (content instanceof String) {
                return ((String) content).trim();
            }
            if (content instanceof JSONArray) {
                JSONArray parts = (JSONArray) content;
                StringBuilder builder = new StringBuilder();
                for (int index = 0; index < parts.length(); index++) {
                    Object item = parts.get(index);
                    if (item instanceof JSONObject) {
                        String text = ((JSONObject) item).optString("text", "");
                        if (!text.isEmpty()) {
                            if (builder.length() > 0) {
                                builder.append('\n');
                            }
                            builder.append(text);
                        }
                    }
                }
                return builder.toString().trim();
            }
            return "";
        }

        private static String httpError(int status, String response) {
            if (status == 401 || status == 403) {
                return "认证失败，请检查 API Key 与服务权限";
            }
            if (status == 429) {
                return "请求过于频繁或额度不足，请稍后重试";
            }
            if (status >= 500) {
                return "模型服务暂时不可用（HTTP " + status + "）";
            }
            String detail = "";
            try {
                JSONObject root = new JSONObject(response);
                JSONObject error = root.optJSONObject("error");
                if (error != null) {
                    detail = error.optString("message", "").replaceAll("\\s+", " ").trim();
                }
            } catch (JSONException ignored) {
                // 未知错误体不直接展示，避免泄露供应商返回的内部信息。
            }
            if (detail.length() > 240) {
                detail = detail.substring(0, 240) + "…";
            }
            return detail.isEmpty()
                    ? "模型服务拒绝了请求（HTTP " + status + "）"
                    : "模型服务拒绝了请求（HTTP " + status + "）：" + detail;
        }

        private void throwIfCancelled() throws ClientException {
            if (cancelled || Thread.currentThread().isInterrupted()) {
                throw new ClientException("请求已取消");
            }
        }
    }

    static final class ClientException extends Exception {
        ClientException(String message) {
            super(message);
        }
    }
}
