package com.onepic.desktop.pet;

import android.content.Context;
import android.content.SharedPreferences;

/**
 * 保存 Android 端按应用隔离的轻量养成状态。
 *
 * <p>所有数值只写入应用私有 SharedPreferences，不访问网络、外部存储或其他应用。
 * 时间推进最多结算一小时，避免设备长时间停用后出现不可恢复的数值下降。</p>
 */
final class PetCareStore {
    private static final String PREFIX = "care_";

    private final SharedPreferences preferences;
    private final PetAssetManifest.CareConfig config;
    private double satiety;
    private double hydration;
    private double vitality;
    private double happiness;
    private double health;
    private double bond;
    private double experience;
    private int coins;
    private long updatedAt;

    PetCareStore(Context context, PetAssetManifest.CareConfig config) {
        this.preferences = context.getSharedPreferences(
                PetOverlayService.PREFS,
                Context.MODE_PRIVATE
        );
        this.config = config;
        satiety = readFloat("satiety", 80f);
        hydration = readFloat("hydration", 80f);
        vitality = readFloat("vitality", 75f);
        happiness = readFloat("happiness", 70f);
        health = readFloat("health", 100f);
        bond = readFloat("bond", 50f);
        experience = readFloat("experience", 0f);
        coins = preferences.getInt(PREFIX + "coins", config.initialCoins);
        updatedAt = preferences.getLong(PREFIX + "updated_at", System.currentTimeMillis());
        clamp();
    }

    boolean enabled() {
        return config.enabled;
    }

    void advance(String activity) {
        long now = System.currentTimeMillis();
        double minutes = Math.min(60d, Math.max(0d, now - updatedAt) / 60000d);
        updatedAt = now;
        if (!enabled() || minutes <= 0d) {
            save();
            return;
        }
        satiety -= 0.08d * minutes;
        hydration -= 0.12d * minutes;
        happiness -= 0.03d * minutes;
        if ("sleep".equals(activity)) {
            vitality += 0.60d * minutes;
            happiness += 0.02d * minutes;
        } else if ("sit".equals(activity)) {
            vitality += 0.12d * minutes;
        } else if ("walk".equals(activity)) {
            vitality -= 0.18d * minutes;
            experience += 0.06d * minutes;
        } else {
            vitality -= 0.08d * minutes;
            experience += 0.02d * minutes;
        }
        if (satiety < 10d || hydration < 10d) {
            health -= 0.12d * minutes;
        } else if (satiety >= 45d && hydration >= 45d) {
            health += 0.02d * minutes;
        }
        clamp();
        save();
    }

    boolean feed() {
        advance("walk");
        if (coins < config.feedCost) {
            return false;
        }
        coins -= config.feedCost;
        satiety += config.feedAmount;
        happiness += config.feedAmount * 0.08d;
        experience += config.feedAmount * 0.02d;
        clamp();
        save();
        return true;
    }

    boolean drink() {
        advance("walk");
        if (coins < config.drinkCost) {
            return false;
        }
        coins -= config.drinkCost;
        hydration += config.drinkAmount;
        experience += config.drinkAmount * 0.01d;
        clamp();
        save();
        return true;
    }

    boolean play() {
        advance("walk");
        if (vitality < Math.max(8d, config.playVitalityCost)) {
            return false;
        }
        vitality -= config.playVitalityCost;
        happiness += config.playHappinessGain;
        bond += 2d;
        experience += 4d;
        clamp();
        save();
        return true;
    }

    void focusReward() {
        advance("sit");
        coins += config.focusRewardCoins;
        experience += config.focusRewardExperience;
        happiness += Math.min(5d, config.focusRewardExperience * 0.1d);
        clamp();
        save();
    }

    String summary() {
        advance("walk");
        return "等级 " + level()
                + " · 金币 " + coins
                + "\n饱腹 " + Math.round(satiety)
                + " · 饮水 " + Math.round(hydration)
                + " · 活力 " + Math.round(vitality)
                + "\n心情 " + Math.round(happiness)
                + " · 健康 " + Math.round(health)
                + " · 好感 " + Math.round(bond);
    }

    private int level() {
        return Math.min(999, (int) Math.floor(experience / 100d) + 1);
    }

    private float readFloat(String name, float fallback) {
        try {
            return preferences.getFloat(PREFIX + name, fallback);
        } catch (ClassCastException ignored) {
            return fallback;
        }
    }

    private void save() {
        preferences.edit()
                .putFloat(PREFIX + "satiety", (float) satiety)
                .putFloat(PREFIX + "hydration", (float) hydration)
                .putFloat(PREFIX + "vitality", (float) vitality)
                .putFloat(PREFIX + "happiness", (float) happiness)
                .putFloat(PREFIX + "health", (float) health)
                .putFloat(PREFIX + "bond", (float) bond)
                .putFloat(PREFIX + "experience", (float) experience)
                .putInt(PREFIX + "coins", coins)
                .putLong(PREFIX + "updated_at", updatedAt)
                .apply();
    }

    private void clamp() {
        satiety = bounded(satiety);
        hydration = bounded(hydration);
        vitality = bounded(vitality);
        happiness = bounded(happiness);
        health = bounded(health);
        bond = bounded(bond);
        experience = Math.max(0d, experience);
        coins = Math.min(999999, Math.max(0, coins));
    }

    private static double bounded(double value) {
        return Math.min(100d, Math.max(0d, value));
    }
}
