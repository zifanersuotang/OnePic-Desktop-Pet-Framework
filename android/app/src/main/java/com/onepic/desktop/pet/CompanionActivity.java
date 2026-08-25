package com.onepic.desktop.pet;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputFilter;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONException;

import java.io.IOException;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Android 智能陪伴中心：管理本地档案、可选记忆、BYOK 设置和聊天历史。
 *
 * <p>打开本页面不会产生网络请求。只有用户启用云聊天、保存 HTTPS 服务与加密密钥，
 * 并明确点击“发送”后才联网。回复只能通过当前宠物已装配动作的硬白名单触发动画。</p>
 */
public final class CompanionActivity extends Activity {
    private static final int COLOR_TEXT = Color.rgb(38, 51, 79);
    private static final int COLOR_MUTED = Color.rgb(88, 100, 126);
    private static final int COLOR_ACCENT = Color.rgb(75, 103, 161);
    private static final int COLOR_CARD = Color.WHITE;

    private final ExecutorService executor = Executors.newSingleThreadExecutor();

    private PetAssetManifest manifest;
    private CompanionStore store;
    private CompanionSettingsStore settingsStore;
    private CompanionSecretStore secretStore;
    private volatile OpenAiCompatibleClient.Call activeCall;
    private volatile long requestToken;
    private volatile boolean destroyed;

    private CheckBox cloudEnabled;
    private CheckBox includeUserProfile;
    private CheckBox includeMemory;
    private CheckBox includeHistory;
    private EditText baseUrlInput;
    private EditText modelInput;
    private EditText apiKeyInput;
    private EditText characterProfileInput;
    private EditText userProfileInput;
    private EditText memoryInput;
    private EditText messageInput;
    private TextView keyStatusView;
    private TextView transcriptView;
    private TextView requestStatusView;
    private Button sendButton;
    private Button cancelButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_SECURE);
        try {
            manifest = PetAssetManifest.load(this);
        } catch (IOException | JSONException error) {
            setContentView(errorContent("宠物素材尚未装配，智能陪伴无法确定 pet_id。"));
            return;
        }
        store = new CompanionStore(this);
        settingsStore = new CompanionSettingsStore(this);
        secretStore = new CompanionSecretStore(this);
        setContentView(buildContent());
        loadState();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        requestToken += 1L;
        OpenAiCompatibleClient.Call call = activeCall;
        activeCall = null;
        if (call != null) {
            call.cancel();
        }
        executor.shutdownNow();
        if (store != null) {
            store.close();
        }
        super.onDestroy();
    }

    private ScrollView buildContent() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(245, 247, 252));

        LinearLayout content = vertical();
        content.setPadding(dp(20), dp(24), dp(20), dp(30));
        scroll.addView(content, fullWidth());

        TextView title = text(manifest.displayName() + " · 智能陪伴", 25, true);
        content.addView(title);
        TextView subtitle = text(
                "本地档案与历史按宠物隔离；云聊天默认关闭，打开页面不会联网。",
                14,
                false
        );
        subtitle.setTextColor(COLOR_MUTED);
        content.addView(subtitle, margins(0, 8, 0, 18));

        LinearLayout networkCard = card("云聊天设置");
        cloudEnabled = checkbox("启用 BYOK 云聊天（默认关闭）");
        networkCard.addView(cloudEnabled, fullWidth());
        baseUrlInput = field("HTTPS 服务地址，例如 https://api.openai.com/v1", 1, 512);
        baseUrlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        networkCard.addView(baseUrlInput, fieldParams());
        modelInput = field("模型名称", 1, 160);
        networkCard.addView(modelInput, fieldParams());
        apiKeyInput = field("API Key（留空则保留已安全保存的密钥）", 1, 4096);
        apiKeyInput.setInputType(
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD
        );
        apiKeyInput.setSaveEnabled(false);
        apiKeyInput.setImportantForAutofill(View.IMPORTANT_FOR_AUTOFILL_NO_EXCLUDE_DESCENDANTS);
        networkCard.addView(apiKeyInput, fieldParams());
        keyStatusView = text("", 13, false);
        keyStatusView.setTextColor(COLOR_MUTED);
        networkCard.addView(keyStatusView, margins(0, 8, 0, 0));

        LinearLayout networkButtons = horizontal();
        networkButtons.addView(
                button("保存设置", ignored -> saveSettings()),
                weightedButton()
        );
        networkButtons.addView(
                button("清除密钥", ignored -> clearApiKey()),
                weightedButtonWithLeftMargin()
        );
        networkCard.addView(networkButtons, margins(0, 10, 0, 0));
        TextView networkNote = text(
                "仅允许 HTTPS；拒绝自动重定向。API Key 使用 Android Keystore AES-GCM 加密，不写入聊天数据库或 APK。",
                12,
                false
        );
        networkNote.setTextColor(COLOR_MUTED);
        networkCard.addView(networkNote, margins(0, 10, 0, 0));
        content.addView(networkCard, sectionParams());

        LinearLayout profileCard = card("角色与发送范围");
        characterProfileInput = field("角色人设（启用云聊天时会发送）", 4, 2400);
        profileCard.addView(characterProfileInput, fieldParams());
        userProfileInput = field("用户档案（默认不发送）", 3, 2400);
        profileCard.addView(userProfileInput, fieldParams());
        memoryInput = field("可选记忆（默认不发送）", 4, 4000);
        profileCard.addView(memoryInput, fieldParams());
        includeUserProfile = checkbox("本次及后续请求包含用户档案");
        includeMemory = checkbox("本次及后续请求包含记忆");
        includeHistory = checkbox("携带最近 30 条聊天历史");
        profileCard.addView(includeUserProfile, fieldParams());
        profileCard.addView(includeMemory, fullWidth());
        profileCard.addView(includeHistory, fullWidth());
        profileCard.addView(
                button("保存本地档案", ignored -> saveSettings()),
                margins(0, 10, 0, 0)
        );
        content.addView(profileCard, sectionParams());

        LinearLayout chatCard = card("聊天");
        transcriptView = text("暂无聊天记录。", 15, false);
        transcriptView.setTextIsSelectable(true);
        transcriptView.setMinHeight(dp(150));
        transcriptView.setPadding(dp(12), dp(10), dp(12), dp(10));
        transcriptView.setBackground(rounded(Color.rgb(246, 248, 253), 12));
        chatCard.addView(transcriptView, fullWidth());
        messageInput = field("输入想对她说的话", 4, 4000);
        chatCard.addView(messageInput, fieldParams());
        requestStatusView = text("", 13, false);
        requestStatusView.setTextColor(COLOR_MUTED);
        chatCard.addView(requestStatusView, fullWidth());

        LinearLayout sendRow = horizontal();
        sendButton = button("发送", ignored -> sendMessage());
        cancelButton = button("停止请求", ignored -> cancelRequest());
        cancelButton.setEnabled(false);
        sendRow.addView(sendButton, weightedButton());
        sendRow.addView(cancelButton, weightedButtonWithLeftMargin());
        chatCard.addView(sendRow, margins(0, 10, 0, 0));

        LinearLayout historyRow = horizontal();
        historyRow.addView(
                button("清空历史", ignored -> confirmClearHistory()),
                weightedButton()
        );
        historyRow.addView(
                button("删除本宠物全部陪伴数据", ignored -> confirmDeleteAll()),
                weightedButtonWithLeftMargin()
        );
        chatCard.addView(historyRow, margins(0, 8, 0, 0));
        content.addView(chatCard, sectionParams());

        TextView privacy = text(
                "发送时只包含：本轮消息、角色人设，以及你主动勾选的用户档案、记忆和最近历史。角色图片、动画帧、按键内容及其他 App 数据不会上传。",
                13,
                false
        );
        privacy.setTextColor(COLOR_MUTED);
        content.addView(privacy, margins(2, 2, 2, 0));
        return scroll;
    }

    private View errorContent(String message) {
        LinearLayout content = vertical();
        content.setGravity(Gravity.CENTER);
        content.setPadding(dp(28), dp(28), dp(28), dp(28));
        content.setBackgroundColor(Color.rgb(245, 247, 252));
        content.addView(text(message, 17, true), fullWidth());
        content.addView(button("返回", ignored -> finish()), margins(0, 18, 0, 0));
        return content;
    }

    private void loadState() {
        String petId = manifest.petId();
        CompanionSettingsStore.Config config = settingsStore.load(petId);
        CompanionStore.Profile profile = store.profile(petId);
        cloudEnabled.setChecked(config.cloudEnabled);
        baseUrlInput.setText(config.baseUrl);
        modelInput.setText(config.model);
        includeUserProfile.setChecked(config.includeUserProfile);
        includeMemory.setChecked(config.includeMemory);
        includeHistory.setChecked(config.includeHistory);
        characterProfileInput.setText(profile.characterProfile);
        userProfileInput.setText(profile.userProfile);
        memoryInput.setText(profile.memoryText);
        apiKeyInput.setText("");
        updateKeyStatus(config);
        renderHistory();
    }

    private void updateKeyStatus(CompanionSettingsStore.Config config) {
        if (config.secretOrigin.isEmpty()) {
            keyStatusView.setText(R.string.companion_key_missing);
            return;
        }
        try {
            boolean present = secretStore.has(manifest.petId(), config.secretOrigin);
            keyStatusView.setText(present
                    ? R.string.companion_key_saved
                    : R.string.companion_key_missing);
        } catch (CompanionSecretStore.SecretException error) {
            keyStatusView.setText(getString(R.string.companion_key_error, error.getMessage()));
        }
    }

    private void saveSettings() {
        try {
            ResolvedSettings resolved = persistSettings(false);
            updateKeyStatus(resolved.config);
            Toast.makeText(this, "设置与本地档案已保存", Toast.LENGTH_SHORT).show();
        } catch (UserVisibleException error) {
            showStatus(error.getMessage(), true);
        }
    }

    private ResolvedSettings persistSettings(boolean requireCloud) throws UserVisibleException {
        String petId = manifest.petId();
        CompanionSettingsStore.Config previous = settingsStore.load(petId);
        String characterProfile = bounded(characterProfileInput.getText().toString(), 2400, "角色人设");
        String userProfile = bounded(userProfileInput.getText().toString(), 2400, "用户档案");
        String memory = bounded(memoryInput.getText().toString(), 4000, "记忆");
        String model = bounded(modelInput.getText().toString(), 160, "模型名称");
        boolean enabled = cloudEnabled.isChecked();
        if (requireCloud && !enabled) {
            throw new UserVisibleException("云聊天仍处于关闭状态");
        }

        final CompanionEndpoint endpoint;
        try {
            endpoint = CompanionEndpoint.parse(baseUrlInput.getText().toString());
        } catch (CompanionEndpoint.ValidationException error) {
            throw new UserVisibleException(error.getMessage());
        }
        if (enabled && model.isEmpty()) {
            throw new UserVisibleException("启用云聊天前需要填写模型名称");
        }

        String newOrigin = endpoint.origin();
        String typedKey = apiKeyInput.getText().toString().trim();
        boolean originChanged = !previous.secretOrigin.isEmpty()
                && !previous.secretOrigin.equals(newOrigin);
        if (enabled && originChanged && typedKey.isEmpty()) {
            throw new UserVisibleException("服务来源已变化，请重新填写 API Key");
        }

        try {
            if (!typedKey.isEmpty()) {
                secretStore.save(petId, newOrigin, typedKey);
            }
            String availableKey = enabled ? secretStore.load(petId, newOrigin) : "";
            if (enabled && availableKey.isEmpty()) {
                throw new UserVisibleException("启用云聊天前需要安全保存 API Key");
            }
            String secretOrigin = !typedKey.isEmpty()
                    || previous.secretOrigin.equals(newOrigin)
                    ? newOrigin
                    : "";
            CompanionSettingsStore.Config next = new CompanionSettingsStore.Config(
                    enabled,
                    endpoint.baseUrl(),
                    model,
                    includeUserProfile.isChecked(),
                    includeMemory.isChecked(),
                    includeHistory.isChecked(),
                    secretOrigin
            );
            store.saveProfile(
                    petId,
                    new CompanionStore.Profile(characterProfile, userProfile, memory)
            );
            settingsStore.save(petId, next);
            if (originChanged) {
                secretStore.delete(petId, previous.secretOrigin);
            }
            apiKeyInput.setText("");
            baseUrlInput.setText(endpoint.baseUrl());
            return new ResolvedSettings(next, endpoint, availableKey);
        } catch (CompanionSecretStore.SecretException error) {
            throw new UserVisibleException(error.getMessage());
        }
    }

    private void clearApiKey() {
        CompanionSettingsStore.Config current = settingsStore.load(manifest.petId());
        String origin = current.secretOrigin;
        if (origin.isEmpty()) {
            try {
                origin = CompanionEndpoint.parse(baseUrlInput.getText().toString()).origin();
            } catch (CompanionEndpoint.ValidationException ignored) {
                // 没有可定位的密钥时仍安全恢复默认关闭状态。
            }
        }
        secretStore.delete(manifest.petId(), origin);
        CompanionSettingsStore.Config next = new CompanionSettingsStore.Config(
                false,
                current.baseUrl,
                current.model,
                current.includeUserProfile,
                current.includeMemory,
                current.includeHistory,
                ""
        );
        settingsStore.save(manifest.petId(), next);
        cloudEnabled.setChecked(false);
        apiKeyInput.setText("");
        updateKeyStatus(next);
        Toast.makeText(this, "API Key 已从本机删除", Toast.LENGTH_SHORT).show();
    }

    private void sendMessage() {
        if (activeCall != null) {
            return;
        }
        final ResolvedSettings resolved;
        try {
            resolved = persistSettings(true);
        } catch (UserVisibleException error) {
            showStatus(error.getMessage(), true);
            return;
        }
        String userMessage;
        try {
            userMessage = bounded(messageInput.getText().toString(), 4000, "消息");
        } catch (UserVisibleException error) {
            showStatus(error.getMessage(), true);
            return;
        }
        if (userMessage.isEmpty()) {
            showStatus("请输入消息", true);
            return;
        }

        String petId = manifest.petId();
        List<CompanionStore.Message> history = resolved.config.includeHistory
                ? store.recentMessages(petId, 30)
                : Collections.emptyList();
        CompanionStore.Profile profile = store.profile(petId);
        String systemPrompt = buildSystemPrompt(profile, resolved.config);
        store.addMessage(petId, "user", userMessage);
        messageInput.setText("");
        renderHistory();

        OpenAiCompatibleClient.Request request = new OpenAiCompatibleClient.Request(
                resolved.endpoint,
                resolved.apiKey,
                resolved.config.model,
                systemPrompt,
                history,
                userMessage,
                manifest.companionActionNames()
        );
        OpenAiCompatibleClient.Call call = new OpenAiCompatibleClient().newCall(request);
        long token = ++requestToken;
        activeCall = call;
        setBusy(true);
        showStatus("正在等待模型回复…", false);

        executor.execute(() -> {
            try {
                OpenAiCompatibleClient.Result result = call.execute();
                if (destroyed || token != requestToken) {
                    return;
                }
                store.addMessage(petId, "assistant", result.text);
                runOnUiThread(() -> completeSuccess(token, result));
            } catch (OpenAiCompatibleClient.ClientException error) {
                if (destroyed || token != requestToken) {
                    return;
                }
                runOnUiThread(() -> completeFailure(token, error.getMessage()));
            }
        });
    }

    private String buildSystemPrompt(
            CompanionStore.Profile profile,
            CompanionSettingsStore.Config config
    ) {
        StringBuilder prompt = new StringBuilder();
        prompt.append("你是名为“")
                .append(manifest.displayName())
                .append("”的桌面陪伴角色。用用户当前语言自然回复。")
                .append("除非用户要求详细说明，普通回复尽量控制在 160 个中文字符以内。")
                .append("不要声称看到了屏幕、按键、图片或其他应用内容，也不要声称执行了设备操作。")
                .append("回复使用纯文本，不输出工具调用。\n");
        if (!profile.characterProfile.isEmpty()) {
            prompt.append("角色人设：").append(profile.characterProfile).append("\n");
        }
        if (config.includeUserProfile && !profile.userProfile.isEmpty()) {
            prompt.append("用户主动提供的档案：").append(profile.userProfile).append("\n");
        }
        if (config.includeMemory && !profile.memoryText.isEmpty()) {
            prompt.append("用户主动选择发送的记忆：").append(profile.memoryText).append("\n");
        }
        List<String> actions = manifest.companionActionNames();
        if (!actions.isEmpty()) {
            prompt.append("如果确实适合，可在回复末尾追加且最多追加一个动作标签：")
                    .append("[[action:动作名]]。动作名只能从以下列表选择：")
                    .append(String.join(", ", actions))
                    .append("。不要解释该标签。没有合适动作时不要添加。\n");
        }
        return prompt.toString().trim();
    }

    private void completeSuccess(long token, OpenAiCompatibleClient.Result result) {
        if (token != requestToken || destroyed) {
            return;
        }
        activeCall = null;
        setBusy(false);
        showStatus("回复完成，已保存在本机历史。", false);
        renderHistory();
        if (PetOverlayService.isRunning()) {
            Intent intent = new Intent(this, PetOverlayService.class)
                    .setAction(PetOverlayService.ACTION_COMPANION_REPLY)
                    .putExtra(PetOverlayService.EXTRA_COMPANION_TEXT, result.text)
                    .putExtra(PetOverlayService.EXTRA_COMPANION_ACTION, result.action);
            startService(intent);
        }
    }

    private void completeFailure(long token, String message) {
        if (token != requestToken || destroyed) {
            return;
        }
        activeCall = null;
        setBusy(false);
        showStatus(message, true);
    }

    private void cancelRequest() {
        requestToken += 1L;
        OpenAiCompatibleClient.Call call = activeCall;
        activeCall = null;
        if (call != null) {
            call.cancel();
        }
        setBusy(false);
        showStatus("请求已取消。", false);
    }

    private void renderHistory() {
        List<CompanionStore.Message> messages = store.recentMessages(manifest.petId(), 30);
        if (messages.isEmpty()) {
            transcriptView.setText("暂无聊天记录。");
            return;
        }
        StringBuilder builder = new StringBuilder();
        for (CompanionStore.Message message : messages) {
            if (builder.length() > 0) {
                builder.append("\n\n");
            }
            builder.append("user".equals(message.role) ? "你：" : manifest.displayName() + "：")
                    .append(message.content);
        }
        transcriptView.setText(builder.toString());
    }

    private void confirmClearHistory() {
        new AlertDialog.Builder(this)
                .setTitle("清空聊天历史？")
                .setMessage("只删除当前宠物的本地聊天记录，不删除人设、记忆或 API Key。")
                .setNegativeButton("取消", null)
                .setPositiveButton("清空", (dialog, which) -> {
                    store.clearMessages(manifest.petId());
                    renderHistory();
                })
                .show();
    }

    private void confirmDeleteAll() {
        new AlertDialog.Builder(this)
                .setTitle("删除全部陪伴数据？")
                .setMessage("将删除当前宠物的人设、用户档案、记忆、历史、设置和 API Key。此操作无法撤销。")
                .setNegativeButton("取消", null)
                .setPositiveButton("全部删除", (dialog, which) -> deleteAll())
                .show();
    }

    private void deleteAll() {
        cancelRequest();
        CompanionSettingsStore.Config config = settingsStore.load(manifest.petId());
        secretStore.delete(manifest.petId(), config.secretOrigin);
        settingsStore.delete(manifest.petId());
        store.deletePet(manifest.petId());
        loadState();
        Toast.makeText(this, "当前宠物的陪伴数据已删除", Toast.LENGTH_SHORT).show();
    }

    private void setBusy(boolean busy) {
        sendButton.setEnabled(!busy);
        cancelButton.setEnabled(busy);
        cloudEnabled.setEnabled(!busy);
        baseUrlInput.setEnabled(!busy);
        modelInput.setEnabled(!busy);
        apiKeyInput.setEnabled(!busy);
    }

    private void showStatus(String message, boolean error) {
        requestStatusView.setText(message == null ? "" : message);
        requestStatusView.setTextColor(error ? Color.rgb(170, 48, 59) : COLOR_MUTED);
    }

    private String bounded(String value, int maximum, String label) throws UserVisibleException {
        String result = value == null ? "" : value.trim();
        if (result.length() > maximum) {
            throw new UserVisibleException(label + "不能超过 " + maximum + " 字");
        }
        return result;
    }

    private LinearLayout card(String title) {
        LinearLayout card = vertical();
        card.setPadding(dp(16), dp(16), dp(16), dp(16));
        card.setBackground(rounded(COLOR_CARD, 16));
        card.setElevation(dp(2));
        card.addView(text(title, 18, true), margins(0, 0, 0, 4));
        return card;
    }

    private EditText field(String hint, int maxLines, int maxLength) {
        EditText input = new EditText(this);
        input.setHint(hint);
        input.setTextSize(15);
        input.setTextColor(COLOR_TEXT);
        input.setHintTextColor(Color.rgb(130, 140, 160));
        input.setSingleLine(maxLines == 1);
        input.setMaxLines(maxLines);
        input.setMinLines(maxLines == 1 ? 1 : Math.min(2, maxLines));
        input.setFilters(new InputFilter[]{new InputFilter.LengthFilter(maxLength)});
        input.setPadding(dp(12), dp(10), dp(12), dp(10));
        input.setBackground(rounded(Color.rgb(246, 248, 253), 10));
        return input;
    }

    private CheckBox checkbox(String label) {
        CheckBox value = new CheckBox(this);
        value.setText(label);
        value.setTextSize(14);
        value.setTextColor(COLOR_TEXT);
        value.setButtonTintList(android.content.res.ColorStateList.valueOf(COLOR_ACCENT));
        return value;
    }

    private Button button(String label, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextSize(14);
        button.setTextColor(COLOR_TEXT);
        button.setGravity(Gravity.CENTER);
        button.setOnClickListener(listener);
        return button;
    }

    private TextView text(String value, int sizeSp, boolean bold) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(sizeSp);
        text.setTextColor(COLOR_TEXT);
        if (bold) {
            text.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        }
        return text;
    }

    private LinearLayout vertical() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        return layout;
    }

    private LinearLayout horizontal() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.HORIZONTAL);
        return layout;
    }

    private GradientDrawable rounded(int color, float radiusDp) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(dp(radiusDp));
        return drawable;
    }

    private LinearLayout.LayoutParams fullWidth() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private LinearLayout.LayoutParams fieldParams() {
        return margins(0, 10, 0, 0);
    }

    private LinearLayout.LayoutParams sectionParams() {
        return margins(0, 0, 0, 16);
    }

    private LinearLayout.LayoutParams margins(int left, int top, int right, int bottom) {
        LinearLayout.LayoutParams params = fullWidth();
        params.setMargins(dp(left), dp(top), dp(right), dp(bottom));
        return params;
    }

    private LinearLayout.LayoutParams weightedButton() {
        return new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
    }

    private LinearLayout.LayoutParams weightedButtonWithLeftMargin() {
        LinearLayout.LayoutParams params = weightedButton();
        params.setMargins(dp(8), 0, 0, 0);
        return params;
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private static final class ResolvedSettings {
        final CompanionSettingsStore.Config config;
        final CompanionEndpoint endpoint;
        final String apiKey;

        ResolvedSettings(
                CompanionSettingsStore.Config config,
                CompanionEndpoint endpoint,
                String apiKey
        ) {
            this.config = config;
            this.endpoint = endpoint;
            this.apiKey = apiKey;
        }
    }

    private static final class UserVisibleException extends Exception {
        UserVisibleException(String message) {
            super(message);
        }
    }
}
