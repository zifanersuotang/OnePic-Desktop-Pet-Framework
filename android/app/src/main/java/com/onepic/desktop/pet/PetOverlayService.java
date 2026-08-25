package com.onepic.desktop.pet;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.content.res.Configuration;
import android.graphics.PixelFormat;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.Settings;
import android.view.GestureDetector;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.WindowManager;

import org.json.JSONException;

import java.io.IOException;

/**
 * 管理用户可见的悬浮桌宠窗口、移动、拖拽、养成交互和前台通知。
 *
 * <p>服务只能由应用界面或通知操作启动；没有悬浮窗权限时立即停止。服务不读取按键、
 * 通讯录、位置或其他应用内容，并通过常驻通知提供互动和停止入口。</p>
 */
public final class PetOverlayService extends Service {
    static final String ACTION_START = "com.onepic.desktop.pet.action.START";
    static final String ACTION_STOP = "com.onepic.desktop.pet.action.STOP";
    static final String ACTION_INTERACT = "com.onepic.desktop.pet.action.INTERACT";
    static final String ACTION_TOGGLE_WALK = "com.onepic.desktop.pet.action.TOGGLE_WALK";
    static final String ACTION_FOCUS = "com.onepic.desktop.pet.action.FOCUS";
    static final String ACTION_CARE_FEED = "com.onepic.desktop.pet.action.CARE_FEED";
    static final String ACTION_CARE_DRINK = "com.onepic.desktop.pet.action.CARE_DRINK";
    static final String ACTION_CARE_PLAY = "com.onepic.desktop.pet.action.CARE_PLAY";
    static final String ACTION_CARE_REST = "com.onepic.desktop.pet.action.CARE_REST";
    static final String ACTION_COMPANION_REPLY =
            "com.onepic.desktop.pet.action.COMPANION_REPLY";
    static final String EXTRA_COMPANION_TEXT = "companion_text";
    static final String EXTRA_COMPANION_ACTION = "companion_action";
    static final String PREFS = "onepic_android_pet";

    private static final String CHANNEL_ID = "onepic_pet_running";
    private static final int NOTIFICATION_ID = 1542;
    private static final long MOVE_INTERVAL_MS = 32L;
    private static final long TOUCH_STABILIZE_MS = 650L;
    private static volatile boolean running;

    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable movementTick = this::advancePosition;

    private WindowManager windowManager;
    private WindowManager.LayoutParams windowParams;
    private PetOverlayView petView;
    private PetCareStore careStore;
    private GestureDetector gestureDetector;
    private SharedPreferences preferences;
    private int direction = -1;
    private long movementPausedUntil;
    private boolean dragging;
    private boolean sideWalk;
    private float touchDownRawX;
    private float touchDownRawY;
    private int touchDownWindowX;
    private int touchDownWindowY;
    private String sideWalkSequence = "";

    @Override
    public void onCreate() {
        super.onCreate();
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        createNotificationChannel();
        promoteToForeground();
        if (!Settings.canDrawOverlays(this)) {
            stopSelf();
            return;
        }
        createOverlay();
        running = petView != null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? ACTION_START : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopSelf();
        } else if (petView != null && ACTION_INTERACT.equals(action)) {
            petView.interact();
        } else if (petView != null && ACTION_TOGGLE_WALK.equals(action)) {
            sideWalk = !sideWalk && !sideWalkSequence.isEmpty();
            petView.setWalkSequence(sideWalk ? sideWalkSequence : "walk");
            preferences.edit().putBoolean("side_walk", sideWalk).apply();
        } else if (petView != null && ACTION_FOCUS.equals(action)) {
            petView.playFocus();
            movementPausedUntil = System.currentTimeMillis() + 12000L;
            if (careStore != null && careStore.enabled()) {
                handler.postDelayed(() -> {
                    if (petView == null || careStore == null) {
                        return;
                    }
                    careStore.focusReward();
                    petView.showMessage("专注完成，获得金币和经验！");
                }, 12000L);
            }
        } else if (petView != null && ACTION_COMPANION_REPLY.equals(action)) {
            String text = intent.getStringExtra(EXTRA_COMPANION_TEXT);
            String actionHint = intent.getStringExtra(EXTRA_COMPANION_ACTION);
            String bubble = CompanionReplyParser.bubbleText(text);
            if (!bubble.isEmpty()) {
                petView.playCompanionReply(actionHint, bubble);
                movementPausedUntil = System.currentTimeMillis() + 3600L;
            }
        } else if (petView != null && careStore != null && careStore.enabled()
                && ACTION_CARE_FEED.equals(action)) {
            boolean success = careStore.feed();
            petView.playCareFeedback(
                    "happy",
                    "idle",
                    success ? "点心收到啦，饱腹度恢复了～" : "金币不够啦，先完成专注吧。"
            );
        } else if (petView != null && careStore != null && careStore.enabled()
                && ACTION_CARE_DRINK.equals(action)) {
            boolean success = careStore.drink();
            petView.playCareFeedback(
                    "happy",
                    "idle",
                    success ? "喝到水啦，谢谢你～" : "暂时买不起饮料。"
            );
        } else if (petView != null && careStore != null && careStore.enabled()
                && ACTION_CARE_PLAY.equals(action)) {
            boolean success = careStore.play();
            petView.playCareFeedback(
                    success ? "jump" : "sleepy",
                    success ? "happy" : "sit",
                    success ? "一起玩真开心！" : "现在有点累，先休息吧。"
            );
        } else if (petView != null && careStore != null && careStore.enabled()
                && ACTION_CARE_REST.equals(action)) {
            movementPausedUntil = System.currentTimeMillis() + 12000L;
            careStore.advance("sleep");
            petView.playCareFeedback("sleep", "sit", "那我休息一会儿。");
            handler.postDelayed(() -> {
                if (petView != null) {
                    petView.resumeWalk();
                }
            }, 12000L);
        }
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onConfigurationChanged(Configuration newConfig) {
        super.onConfigurationChanged(newConfig);
        clampAndUpdate();
    }

    @Override
    public void onDestroy() {
        running = false;
        handler.removeCallbacksAndMessages(null);
        gestureDetector = null;
        if (petView != null) {
            savePosition();
            if (careStore != null && careStore.enabled()) {
                careStore.advance("walk");
            }
            petView.release();
            try {
                windowManager.removeView(petView);
            } catch (IllegalArgumentException ignored) {
                // 窗口已被系统移除时无需重复处理。
            }
            petView = null;
            careStore = null;
        }
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }

    private void createOverlay() {
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        try {
            petView = new PetOverlayView(this);
            careStore = new PetCareStore(this, petView.careConfig());
        } catch (IOException | JSONException error) {
            stopSelf();
            return;
        }

        int width = dp(220);
        int height = dp(280);
        windowParams = new WindowManager.LayoutParams(
                width,
                height,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
                PixelFormat.TRANSLUCENT
        );
        windowParams.gravity = Gravity.TOP | Gravity.START;
        int defaultX = Math.max(0, screenWidth() - width - dp(12));
        int defaultY = Math.max(0, screenHeight() - height - dp(96));
        windowParams.x = preferences.getInt("window_x", defaultX);
        windowParams.y = preferences.getInt("window_y", defaultY);
        sideWalkSequence = petView.preferredSideWalkSequence();
        sideWalk = preferences.getBoolean("side_walk", false)
                && !sideWalkSequence.isEmpty();
        petView.setWalkSequence(sideWalk ? sideWalkSequence : "walk");
        gestureDetector = createGestureDetector();
        petView.setOnTouchListener(this::handleTouch);

        try {
            windowManager.addView(petView, windowParams);
        } catch (RuntimeException error) {
            petView.release();
            petView = null;
            gestureDetector = null;
            stopSelf();
            return;
        }
        clampAndUpdate();
        handler.postDelayed(movementTick, MOVE_INTERVAL_MS);
    }

    private GestureDetector createGestureDetector() {
        GestureDetector detector = new GestureDetector(
                this,
                new GestureDetector.SimpleOnGestureListener() {
                    @Override
                    public boolean onDown(MotionEvent event) {
                        return true;
                    }

                    @Override
                    public boolean onSingleTapConfirmed(MotionEvent event) {
                        if (petView == null || dragging) {
                            return false;
                        }
                        petView.performClick();
                        return true;
                    }

                    @Override
                    public boolean onDoubleTap(MotionEvent event) {
                        openCompanionFromOverlay();
                        return true;
                    }
                },
                handler
        );
        detector.setIsLongpressEnabled(false);
        return detector;
    }

    private boolean handleTouch(android.view.View view, MotionEvent event) {
        boolean gestureHandled = gestureDetector != null
                && gestureDetector.onTouchEvent(event);
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                movementPausedUntil = Math.max(
                        movementPausedUntil,
                        System.currentTimeMillis() + TOUCH_STABILIZE_MS
                );
                touchDownRawX = event.getRawX();
                touchDownRawY = event.getRawY();
                touchDownWindowX = windowParams.x;
                touchDownWindowY = windowParams.y;
                dragging = false;
                return true;
            case MotionEvent.ACTION_MOVE:
                float deltaX = event.getRawX() - touchDownRawX;
                float deltaY = event.getRawY() - touchDownRawY;
                if (!dragging && Math.hypot(deltaX, deltaY) > dp(6)) {
                    dragging = true;
                    petView.setDragging(true);
                }
                if (dragging) {
                    windowParams.x = touchDownWindowX + Math.round(deltaX);
                    windowParams.y = touchDownWindowY + Math.round(deltaY);
                    clampAndUpdate();
                }
                return true;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (dragging) {
                    dragging = false;
                    petView.setDragging(false);
                    savePosition();
                }
                return true;
            default:
                return gestureHandled;
        }
    }

    private void openCompanionFromOverlay() {
        if (petView == null
                || !petView.isAttachedToWindow()
                || !Settings.canDrawOverlays(this)) {
            return;
        }
        Intent intent = new Intent(this, CompanionActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException | SecurityException error) {
            petView.showMessage("聊天页暂时无法打开，请从通知栏进入。");
        }
    }

    private void advancePosition() {
        if (petView == null || windowParams == null) {
            return;
        }
        if (!dragging && System.currentTimeMillis() >= movementPausedUntil) {
            int maximumX = Math.max(0, screenWidth() - windowParams.width);
            int step = Math.max(1, Math.round(dp(1.35f)));
            windowParams.x += direction * step;
            if (windowParams.x <= 0) {
                windowParams.x = 0;
                direction = 1;
            } else if (windowParams.x >= maximumX) {
                windowParams.x = maximumX;
                direction = -1;
            }
            petView.setMirrored(direction < 0);
            updateOverlay();
        }
        handler.postDelayed(movementTick, MOVE_INTERVAL_MS);
    }

    private void clampAndUpdate() {
        if (windowParams == null) {
            return;
        }
        windowParams.x = Math.max(
                0,
                Math.min(windowParams.x, Math.max(0, screenWidth() - windowParams.width))
        );
        windowParams.y = Math.max(
                0,
                Math.min(windowParams.y, Math.max(0, screenHeight() - windowParams.height))
        );
        updateOverlay();
    }

    private void updateOverlay() {
        if (petView == null || windowManager == null) {
            return;
        }
        try {
            windowManager.updateViewLayout(petView, windowParams);
        } catch (IllegalArgumentException ignored) {
            // 系统正在销毁窗口时忽略最后一次位置更新。
        }
    }

    private void savePosition() {
        if (windowParams == null || preferences == null) {
            return;
        }
        preferences.edit()
                .putInt("window_x", windowParams.x)
                .putInt("window_y", windowParams.y)
                .apply();
    }

    private void createNotificationChannel() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription(getString(R.string.notification_channel_description));
        manager.createNotificationChannel(channel);
    }

    private void promoteToForeground() {
        Notification notification = buildNotification();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            );
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private Notification buildNotification() {
        Intent openIntent = new Intent(this, MainActivity.class);
        PendingIntent open = PendingIntent.getActivity(
                this,
                1,
                openIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        PendingIntent chat = PendingIntent.getActivity(
                this,
                4,
                new Intent(this, CompanionActivity.class),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        PendingIntent interact = serviceAction(ACTION_INTERACT, 2);
        PendingIntent stop = serviceAction(ACTION_STOP, 3);
        return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(R.drawable.ic_pet)
                .setContentTitle(applicationLabel() + "正在陪伴你")
                .setContentText(getString(R.string.notification_text))
                .setContentIntent(open)
                .setOngoing(true)
                .setCategory(Notification.CATEGORY_SERVICE)
                .addAction(new Notification.Action.Builder(null, "聊天", chat).build())
                .addAction(new Notification.Action.Builder(null, "互动", interact).build())
                .addAction(new Notification.Action.Builder(null, "停止", stop).build())
                .build();
    }

    static boolean isRunning() {
        return running;
    }

    private String applicationLabel() {
        return getApplicationInfo().loadLabel(getPackageManager()).toString();
    }

    private PendingIntent serviceAction(String action, int requestCode) {
        Intent intent = new Intent(this, PetOverlayService.class).setAction(action);
        return PendingIntent.getService(
                this,
                requestCode,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
    }

    private int screenWidth() {
        return getResources().getDisplayMetrics().widthPixels;
    }

    private int screenHeight() {
        return getResources().getDisplayMetrics().heightPixels;
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
