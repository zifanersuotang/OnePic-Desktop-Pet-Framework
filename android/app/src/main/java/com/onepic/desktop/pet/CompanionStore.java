package com.onepic.desktop.pet;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/** 按 pet_id 保存 Android 端角色档案、用户档案、记忆和聊天历史。 */
final class CompanionStore extends SQLiteOpenHelper {
    private static final String DATABASE_NAME = "onepic_companion.db";
    private static final int DATABASE_VERSION = 1;
    private static final int MAX_MESSAGES_PER_PET = 500;

    CompanionStore(Context context) {
        super(context.getApplicationContext(), DATABASE_NAME, null, DATABASE_VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase database) {
        database.execSQL(
                "CREATE TABLE profiles ("
                        + "pet_id TEXT PRIMARY KEY NOT NULL,"
                        + "character_profile TEXT NOT NULL DEFAULT '',"
                        + "user_profile TEXT NOT NULL DEFAULT '',"
                        + "memory_text TEXT NOT NULL DEFAULT '',"
                        + "updated_at INTEGER NOT NULL)"
        );
        database.execSQL(
                "CREATE TABLE messages ("
                        + "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                        + "pet_id TEXT NOT NULL,"
                        + "role TEXT NOT NULL,"
                        + "content TEXT NOT NULL,"
                        + "created_at INTEGER NOT NULL)"
        );
        database.execSQL(
                "CREATE INDEX messages_pet_time ON messages(pet_id, created_at, id)"
        );
    }

    @Override
    public void onUpgrade(SQLiteDatabase database, int oldVersion, int newVersion) {
        if (oldVersion != newVersion) {
            throw new IllegalStateException("不支持的陪伴数据库升级路径");
        }
    }

    Profile profile(String petId) {
        try (Cursor cursor = getReadableDatabase().query(
                "profiles",
                new String[]{"character_profile", "user_profile", "memory_text"},
                "pet_id = ?",
                new String[]{petId},
                null,
                null,
                null,
                "1"
        )) {
            if (!cursor.moveToFirst()) {
                return new Profile("", "", "");
            }
            return new Profile(cursor.getString(0), cursor.getString(1), cursor.getString(2));
        }
    }

    void saveProfile(String petId, Profile profile) {
        ContentValues values = new ContentValues();
        values.put("pet_id", petId);
        values.put("character_profile", bounded(profile.characterProfile, 2400));
        values.put("user_profile", bounded(profile.userProfile, 2400));
        values.put("memory_text", bounded(profile.memoryText, 4000));
        values.put("updated_at", System.currentTimeMillis());
        getWritableDatabase().insertWithOnConflict(
                "profiles",
                null,
                values,
                SQLiteDatabase.CONFLICT_REPLACE
        );
    }

    void addMessage(String petId, String role, String content) {
        if (!"user".equals(role) && !"assistant".equals(role)) {
            throw new IllegalArgumentException("聊天角色无效");
        }
        String safeContent = bounded(content, 12000);
        if (safeContent.isEmpty()) {
            return;
        }
        SQLiteDatabase database = getWritableDatabase();
        database.beginTransaction();
        try {
            ContentValues values = new ContentValues();
            values.put("pet_id", petId);
            values.put("role", role);
            values.put("content", safeContent);
            values.put("created_at", System.currentTimeMillis());
            database.insertOrThrow("messages", null, values);
            database.execSQL(
                    "DELETE FROM messages WHERE pet_id = ? AND id NOT IN ("
                            + "SELECT id FROM messages WHERE pet_id = ? "
                            + "ORDER BY created_at DESC, id DESC LIMIT "
                            + MAX_MESSAGES_PER_PET + ")",
                    new Object[]{petId, petId}
            );
            database.setTransactionSuccessful();
        } finally {
            database.endTransaction();
        }
    }

    List<Message> recentMessages(String petId, int limit) {
        int safeLimit = Math.min(30, Math.max(0, limit));
        if (safeLimit == 0) {
            return Collections.emptyList();
        }
        List<Message> reverse = new ArrayList<>();
        try (Cursor cursor = getReadableDatabase().query(
                "messages",
                new String[]{"role", "content", "created_at"},
                "pet_id = ?",
                new String[]{petId},
                null,
                null,
                "created_at DESC, id DESC",
                Integer.toString(safeLimit)
        )) {
            while (cursor.moveToNext()) {
                reverse.add(new Message(cursor.getString(0), cursor.getString(1), cursor.getLong(2)));
            }
        }
        Collections.reverse(reverse);
        return Collections.unmodifiableList(reverse);
    }

    void clearMessages(String petId) {
        getWritableDatabase().delete("messages", "pet_id = ?", new String[]{petId});
    }

    void deletePet(String petId) {
        SQLiteDatabase database = getWritableDatabase();
        database.beginTransaction();
        try {
            database.delete("messages", "pet_id = ?", new String[]{petId});
            database.delete("profiles", "pet_id = ?", new String[]{petId});
            database.setTransactionSuccessful();
        } finally {
            database.endTransaction();
        }
    }

    private static String bounded(String value, int maximum) {
        String result = value == null ? "" : value.trim();
        return result.length() <= maximum ? result : result.substring(0, maximum);
    }

    static final class Profile {
        final String characterProfile;
        final String userProfile;
        final String memoryText;

        Profile(String characterProfile, String userProfile, String memoryText) {
            this.characterProfile = characterProfile == null ? "" : characterProfile;
            this.userProfile = userProfile == null ? "" : userProfile;
            this.memoryText = memoryText == null ? "" : memoryText;
        }
    }

    static final class Message {
        final String role;
        final String content;
        final long createdAt;

        Message(String role, String content, long createdAt) {
            this.role = role;
            this.content = content;
            this.createdAt = createdAt;
        }
    }
}
