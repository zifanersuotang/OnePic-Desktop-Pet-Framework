package com.onepic.desktop.pet;

import android.content.Context;
import android.content.res.AssetManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.os.Handler;
import android.os.Looper;
import android.util.LruCache;
import android.view.View;

import org.json.JSONException;

import java.io.IOException;
import java.io.InputStream;

/**
 * 绘制透明精灵、动画序列、养成反馈与短时互动气泡。
 *
 * <p>位图按当前悬浮窗尺寸降采样，并在有限内存缓存中复用。视图不访问网络、不读取
 * 外部存储，也不会加载未被脱敏运行清单引用的文件。</p>
 */
final class PetOverlayView extends View {
    private static final long BUBBLE_DURATION_MS = 3400L;

    private final PetAssetManifest manifest;
    private final AssetManager assets;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Paint bitmapPaint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.FILTER_BITMAP_FLAG);
    private final Paint bubblePaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF bitmapDestination = new RectF();
    private final RectF bubbleBounds = new RectF();
    private final LruCache<String, Bitmap> bitmapCache;
    private final Runnable frameTick = this::advanceFrame;

    private String walkSequence = "walk";
    private String currentName = "walk";
    private PetAssetManifest.Sequence currentSequence;
    private int frameIndex;
    private boolean returnToWalk;
    private boolean mirrored;
    private String bubbleText = "";
    private long bubbleExpiresAt;

    PetOverlayView(Context context) throws IOException, JSONException {
        super(context);
        setLayerType(View.LAYER_TYPE_SOFTWARE, null);
        setBackgroundColor(Color.TRANSPARENT);
        manifest = PetAssetManifest.load(context);
        assets = context.getAssets();
        int cacheKilobytes = Math.min(24 * 1024, (int) (Runtime.getRuntime().maxMemory() / 1024L / 12L));
        bitmapCache = new LruCache<String, Bitmap>(Math.max(8 * 1024, cacheKilobytes)) {
            @Override
            protected int sizeOf(String key, Bitmap value) {
                return value.getAllocationByteCount() / 1024;
            }
        };
        bubblePaint.setColor(Color.argb(232, 255, 255, 255));
        textPaint.setColor(Color.rgb(38, 51, 79));
        textPaint.setTextAlign(Paint.Align.CENTER);
        textPaint.setTextSize(dp(14));
        playLoop(walkSequence);
    }

    String displayName() {
        return manifest.displayName();
    }

    PetAssetManifest.CareConfig careConfig() {
        return manifest.careConfig();
    }

    void setMirrored(boolean mirrored) {
        if (this.mirrored != mirrored) {
            this.mirrored = mirrored;
            invalidate();
        }
    }

    void setWalkSequence(String name) {
        walkSequence = manifest.hasSequence(name) ? name : "walk";
        playLoop(walkSequence);
        showDialogue(walkSequence);
    }

    boolean hasSequence(String name) {
        return manifest.hasSequence(name);
    }

    String preferredSideWalkSequence() {
        return manifest.preferredSideWalkSequence();
    }

    void interact() {
        String action = manifest.randomInteraction();
        playOnce(action);
        showDialogue(action);
    }

    @Override
    public boolean performClick() {
        super.performClick();
        interact();
        return true;
    }

    void playFocus() {
        if (manifest.hasSequence("focus_work")) {
            playLoop("focus_work");
            showDialogue("focus_work");
            handler.postDelayed(this::resumeWalk, 12000L);
        } else {
            interact();
        }
    }

    void playCareFeedback(String preferred, String fallback, String message) {
        String action = manifest.hasSequence(preferred) ? preferred : fallback;
        if (manifest.hasSequence(action)) {
            playOnce(action);
        }
        showMessage(message);
    }

    void playCompanionReply(String requestedAction, String message) {
        if (manifest.isCompanionActionAllowed(requestedAction)) {
            playOnce(requestedAction);
        }
        showMessage(message);
    }

    void showMessage(String message) {
        if (message == null || message.trim().isEmpty()) {
            return;
        }
        bubbleText = message.trim();
        bubbleExpiresAt = System.currentTimeMillis() + BUBBLE_DURATION_MS;
        invalidate();
    }

    void setDragging(boolean dragging) {
        if (dragging && manifest.hasSequence("drag")) {
            playLoop("drag");
        } else if (!dragging) {
            resumeWalk();
        }
    }

    void resumeWalk() {
        playLoop(walkSequence);
    }

    void release() {
        handler.removeCallbacksAndMessages(null);
        for (Bitmap bitmap : bitmapCache.snapshot().values()) {
            if (!bitmap.isRecycled()) {
                bitmap.recycle();
            }
        }
        bitmapCache.evictAll();
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        if (currentSequence == null || currentSequence.frames.isEmpty()) {
            return;
        }
        String framePath = currentSequence.frames.get(
                Math.min(frameIndex, currentSequence.frames.size() - 1)
        );
        Bitmap bitmap = loadBitmap(framePath);
        if (bitmap == null) {
            return;
        }

        boolean bubbleVisible = !bubbleText.isEmpty()
                && System.currentTimeMillis() < bubbleExpiresAt;
        float bubbleHeight = bubbleVisible ? dp(66) : 0f;
        float availableWidth = getWidth() - dp(10);
        float availableHeight = getHeight() - bubbleHeight - dp(6);
        float scale = Math.min(
                availableWidth / bitmap.getWidth(),
                availableHeight / bitmap.getHeight()
        );
        float drawWidth = bitmap.getWidth() * scale;
        float drawHeight = bitmap.getHeight() * scale;
        float left = (getWidth() - drawWidth) / 2f;
        float top = bubbleHeight + Math.max(0f, availableHeight - drawHeight);
        bitmapDestination.set(left, top, left + drawWidth, top + drawHeight);

        canvas.save();
        if (mirrored) {
            canvas.scale(-1f, 1f, getWidth() / 2f, getHeight() / 2f);
        }
        canvas.drawBitmap(bitmap, null, bitmapDestination, bitmapPaint);
        canvas.restore();

        if (bubbleVisible) {
            drawBubble(canvas);
            postInvalidateDelayed(250L);
        } else if (!bubbleText.isEmpty()) {
            bubbleText = "";
        }
    }

    private void playLoop(String name) {
        setSequence(name, false);
    }

    private void playOnce(String name) {
        setSequence(name, true);
    }

    private void setSequence(String name, boolean returnToWalk) {
        handler.removeCallbacks(frameTick);
        currentName = name;
        currentSequence = manifest.sequence(name);
        frameIndex = 0;
        this.returnToWalk = returnToWalk;
        invalidate();
        handler.postDelayed(frameTick, currentSequence.frameDurationMs);
    }

    private void advanceFrame() {
        if (currentSequence == null || currentSequence.frames.isEmpty()) {
            return;
        }
        frameIndex += 1;
        if (frameIndex >= currentSequence.frames.size()) {
            if (returnToWalk || !currentSequence.loop) {
                resumeWalk();
                return;
            }
            frameIndex = 0;
        }
        invalidate();
        handler.postDelayed(frameTick, currentSequence.frameDurationMs);
    }

    private void showDialogue(String action) {
        String text = manifest.randomDialogue(action);
        if (text.isEmpty()) {
            return;
        }
        bubbleText = text;
        bubbleExpiresAt = System.currentTimeMillis() + BUBBLE_DURATION_MS;
        invalidate();
    }

    private void drawBubble(Canvas canvas) {
        float margin = dp(8);
        float bottom = dp(58);
        bubbleBounds.set(margin, dp(4), getWidth() - margin, bottom);
        canvas.drawRoundRect(bubbleBounds, dp(14), dp(14), bubblePaint);

        String[] lines = splitBubbleText(bubbleText);
        if (lines.length == 1) {
            canvas.drawText(lines[0], getWidth() / 2f, dp(37), textPaint);
        } else {
            canvas.drawText(lines[0], getWidth() / 2f, dp(27), textPaint);
            canvas.drawText(lines[1], getWidth() / 2f, dp(46), textPaint);
        }
    }

    private String[] splitBubbleText(String text) {
        int maximum = 13;
        if (text.length() <= maximum) {
            return new String[]{text};
        }
        int split = Math.min(maximum, text.length());
        return new String[]{
                text.substring(0, split),
                text.substring(split, Math.min(text.length(), split + maximum))
        };
    }

    private Bitmap loadBitmap(String relativePath) {
        Bitmap cached = bitmapCache.get(relativePath);
        if (cached != null && !cached.isRecycled()) {
            return cached;
        }
        String path = PetAssetManifest.ASSET_ROOT + relativePath;
        try {
            BitmapFactory.Options bounds = new BitmapFactory.Options();
            bounds.inJustDecodeBounds = true;
            try (InputStream input = assets.open(path)) {
                BitmapFactory.decodeStream(input, null, bounds);
            }
            int targetWidth = Math.max(1, getWidth());
            int targetHeight = Math.max(1, getHeight());
            int sample = 1;
            while (
                    bounds.outWidth / (sample * 2) >= targetWidth
                            && bounds.outHeight / (sample * 2) >= targetHeight
            ) {
                sample *= 2;
            }
            BitmapFactory.Options decode = new BitmapFactory.Options();
            decode.inSampleSize = sample;
            decode.inPreferredConfig = Bitmap.Config.ARGB_8888;
            Bitmap bitmap;
            try (InputStream input = assets.open(path)) {
                bitmap = BitmapFactory.decodeStream(input, null, decode);
            }
            if (bitmap != null) {
                bitmapCache.put(relativePath, bitmap);
            }
            return bitmap;
        } catch (IOException ignored) {
            return null;
        }
    }

    private float dp(float value) {
        return value * getResources().getDisplayMetrics().density;
    }
}
