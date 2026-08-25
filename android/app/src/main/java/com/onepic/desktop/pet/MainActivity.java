package com.onepic.desktop.pet;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONException;

import java.io.IOException;

/**
 * 提供悬浮窗授权、启动、互动、走路切换、养成、专注和停止入口。
 *
 * <p>应用不会诱导用户授予无关权限。悬浮窗与通知权限均由用户在可见界面主动选择，
 * 桌宠停止后不保留后台任务。</p>
 */
public final class MainActivity extends Activity {
    private static final int NOTIFICATION_PERMISSION_REQUEST = 7001;

    private TextView statusView;
    private PetAssetManifest assetManifest;
    private PetCareStore careStore;
    private boolean assetsReady;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        assetsReady = verifyPetAssets();
        setContentView(buildContent());
    }

    @Override
    protected void onResume() {
        super.onResume();
        updateStatus();
    }

    private ScrollView buildContent() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(Color.rgb(245, 247, 252));

        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(dp(24), dp(28), dp(24), dp(28));
        scroll.addView(
                content,
                new ScrollView.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT
                )
        );

        TextView title = text(applicationLabel() + " · Android 悬浮桌宠", 24, true);
        content.addView(title);

        TextView description = text(
                "桌宠会显示在其他 App 上方。拖动可移动，单击互动，双击角色打开智能陪伴；也可从常驻通知随时停止。",
                16,
                false
        );
        LinearLayout.LayoutParams descriptionParams = fullWidth();
        descriptionParams.setMargins(0, dp(12), 0, dp(18));
        content.addView(description, descriptionParams);

        statusView = text("", 15, false);
        statusView.setPadding(dp(14), dp(12), dp(14), dp(12));
        statusView.setBackgroundColor(Color.rgb(232, 237, 248));
        content.addView(statusView, fullWidth());

        content.addView(
                button("1. 授予悬浮窗权限", ignored -> openOverlayPermission()),
                spacedButton()
        );
        content.addView(
                button("2. 开启桌宠", ignored -> startPet()),
                spacedButton()
        );
        content.addView(
                button("互动一次", ignored -> sendServiceAction(PetOverlayService.ACTION_INTERACT)),
                spacedButton()
        );
        content.addView(
                button(
                        "切换正面 / 侧面走路",
                        ignored -> sendServiceAction(PetOverlayService.ACTION_TOGGLE_WALK)
                ),
                spacedButton()
        );
        content.addView(
                button("开始专注陪伴", ignored -> sendServiceAction(PetOverlayService.ACTION_FOCUS)),
                spacedButton()
        );
        content.addView(
                button("打开智能陪伴 / AI 聊天", ignored -> openCompanion()),
                spacedButton()
        );
        if (careStore != null && careStore.enabled()) {
            TextView careTitle = text("养成互动", 18, true);
            LinearLayout.LayoutParams careTitleParams = fullWidth();
            careTitleParams.setMargins(0, dp(22), 0, 0);
            content.addView(careTitle, careTitleParams);
            content.addView(
                    button("喂小点心", ignored -> sendServiceAction(
                            PetOverlayService.ACTION_CARE_FEED
                    )),
                    spacedButton()
            );
            content.addView(
                    button("喝水", ignored -> sendServiceAction(
                            PetOverlayService.ACTION_CARE_DRINK
                    )),
                    spacedButton()
            );
            content.addView(
                    button("一起玩", ignored -> sendServiceAction(
                            PetOverlayService.ACTION_CARE_PLAY
                    )),
                    spacedButton()
            );
            content.addView(
                    button("休息一下", ignored -> sendServiceAction(
                            PetOverlayService.ACTION_CARE_REST
                    )),
                    spacedButton()
            );
        }
        content.addView(
                button("停止并关闭桌宠", ignored -> sendServiceAction(PetOverlayService.ACTION_STOP)),
                spacedButton()
        );

        TextView privacy = text(
                "隐私说明：云聊天默认关闭，只有配置服务并点击发送才会联网；不会上传角色图片、读取按键内容、通讯录、位置或其他 App 数据。",
                14,
                false
        );
        LinearLayout.LayoutParams privacyParams = fullWidth();
        privacyParams.setMargins(0, dp(22), 0, 0);
        content.addView(privacy, privacyParams);
        return scroll;
    }

    private void openOverlayPermission() {
        Intent intent = new Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName())
        );
        startActivity(intent);
    }

    private void startPet() {
        if (!assetsReady) {
            updateStatus();
            return;
        }
        if (!Settings.canDrawOverlays(this)) {
            openOverlayPermission();
            return;
        }
        requestNotificationPermissionIfNeeded();
        Intent intent = new Intent(this, PetOverlayService.class)
                .setAction(PetOverlayService.ACTION_START);
        startForegroundService(intent);
        statusView.postDelayed(this::updateStatus, 350L);
    }

    private void requestNotificationPermissionIfNeeded() {
        if (
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                        && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                        != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(
                    new String[]{Manifest.permission.POST_NOTIFICATIONS},
                    NOTIFICATION_PERMISSION_REQUEST
            );
        }
    }

    private void sendServiceAction(String action) {
        if (!isPetRunning()) {
            updateStatus();
            return;
        }
        startService(new Intent(this, PetOverlayService.class).setAction(action));
        statusView.postDelayed(this::updateStatus, 250L);
    }

    private void openCompanion() {
        if (!assetsReady) {
            updateStatus();
            return;
        }
        startActivity(new Intent(this, CompanionActivity.class));
    }

    private boolean verifyPetAssets() {
        try {
            assetManifest = PetAssetManifest.load(this);
            careStore = new PetCareStore(this, assetManifest.careConfig());
            return true;
        } catch (IOException | JSONException error) {
            assetManifest = null;
            careStore = null;
            return false;
        }
    }

    private boolean isPetRunning() {
        return PetOverlayService.isRunning();
    }

    private void updateStatus() {
        if (statusView == null) {
            return;
        }
        boolean overlayAllowed = Settings.canDrawOverlays(this);
        StringBuilder status = new StringBuilder();
        status.append(assetsReady ? "✓ 宠物素材已就绪" : "✗ 宠物素材尚未装配");
        status.append(overlayAllowed ? "\n✓ 悬浮窗权限已开启" : "\n○ 尚未授予悬浮窗权限");
        status.append(isPetRunning() ? "\n✓ 桌宠正在运行" : "\n○ 桌宠当前未运行");
        if (assetManifest != null && assetManifest.careConfig().enabled) {
            careStore = new PetCareStore(this, assetManifest.careConfig());
            status.append("\n\n养成状态\n").append(careStore.summary());
        }
        statusView.setText(status.toString());
    }

    private Button button(String label, android.view.View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextSize(16);
        button.setAllCaps(false);
        button.setGravity(Gravity.CENTER);
        button.setOnClickListener(listener);
        button.setEnabled(assetsReady || label.startsWith("1."));
        return button;
    }

    private TextView text(String value, int sizeSp, boolean bold) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextSize(sizeSp);
        text.setTextColor(Color.rgb(38, 51, 79));
        if (bold) {
            text.setTypeface(text.getTypeface(), android.graphics.Typeface.BOLD);
        }
        return text;
    }

    private LinearLayout.LayoutParams fullWidth() {
        return new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
        );
    }

    private LinearLayout.LayoutParams spacedButton() {
        LinearLayout.LayoutParams params = fullWidth();
        params.setMargins(0, dp(12), 0, 0);
        return params;
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private String applicationLabel() {
        return getApplicationInfo().loadLabel(getPackageManager()).toString();
    }
}
