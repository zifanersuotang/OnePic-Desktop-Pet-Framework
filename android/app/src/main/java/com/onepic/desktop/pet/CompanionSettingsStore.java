package com.onepic.desktop.pet;

import android.content.Context;
import android.content.SharedPreferences;

/** 保存不含密钥的、按宠物隔离的 Android 智能陪伴设置。 */
final class CompanionSettingsStore {
    private static final String PREFS = "onepic_companion_settings";

    private final SharedPreferences preferences;

    CompanionSettingsStore(Context context) {
        preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    Config load(String petId) {
        String prefix = prefix(petId);
        return new Config(
                preferences.getBoolean(prefix + "cloud_enabled", false),
                preferences.getString(prefix + "base_url", "https://api.openai.com/v1"),
                preferences.getString(prefix + "model", ""),
                preferences.getBoolean(prefix + "include_user_profile", false),
                preferences.getBoolean(prefix + "include_memory", false),
                preferences.getBoolean(prefix + "include_history", false),
                preferences.getString(prefix + "secret_origin", "")
        );
    }

    void save(String petId, Config config) {
        String prefix = prefix(petId);
        preferences.edit()
                .putBoolean(prefix + "cloud_enabled", config.cloudEnabled)
                .putString(prefix + "base_url", config.baseUrl)
                .putString(prefix + "model", config.model)
                .putBoolean(prefix + "include_user_profile", config.includeUserProfile)
                .putBoolean(prefix + "include_memory", config.includeMemory)
                .putBoolean(prefix + "include_history", config.includeHistory)
                .putString(prefix + "secret_origin", config.secretOrigin)
                .apply();
    }

    void delete(String petId) {
        String prefix = prefix(petId);
        SharedPreferences.Editor editor = preferences.edit();
        for (String key : preferences.getAll().keySet()) {
            if (key.startsWith(prefix)) {
                editor.remove(key);
            }
        }
        editor.apply();
    }

    private static String prefix(String petId) {
        return "pet." + petId + ".";
    }

    static final class Config {
        final boolean cloudEnabled;
        final String baseUrl;
        final String model;
        final boolean includeUserProfile;
        final boolean includeMemory;
        final boolean includeHistory;
        final String secretOrigin;

        Config(
                boolean cloudEnabled,
                String baseUrl,
                String model,
                boolean includeUserProfile,
                boolean includeMemory,
                boolean includeHistory,
                String secretOrigin
        ) {
            this.cloudEnabled = cloudEnabled;
            this.baseUrl = baseUrl == null ? "" : baseUrl;
            this.model = model == null ? "" : model;
            this.includeUserProfile = includeUserProfile;
            this.includeMemory = includeMemory;
            this.includeHistory = includeHistory;
            this.secretOrigin = secretOrigin == null ? "" : secretOrigin;
        }
    }
}
