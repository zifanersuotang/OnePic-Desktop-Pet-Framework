package com.onepic.desktop.pet;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.security.MessageDigest;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/**
 * 用 Android Keystore AES-GCM 保存按宠物和服务来源绑定的 API Key。
 *
 * <p>SharedPreferences 只保存随机 IV 与密文；Keystore 密钥不可导出。解密时使用
 * pet_id 与规范化 origin 作为附加认证数据，密文被复制到其他宠物或服务时会失败。</p>
 */
final class CompanionSecretStore {
    private static final String ANDROID_KEYSTORE = "AndroidKeyStore";
    private static final String TRANSFORMATION = "AES/GCM/NoPadding";
    private static final String PREFS = "onepic_companion_secrets";
    private static final int FORMAT_VERSION = 1;
    private static final int GCM_TAG_BITS = 128;

    private final Context context;
    private final SharedPreferences preferences;

    CompanionSecretStore(Context context) {
        this.context = context.getApplicationContext();
        preferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    void save(String petId, String origin, String apiKey) throws SecretException {
        String value = apiKey == null ? "" : apiKey.trim();
        if (value.isEmpty()) {
            throw new SecretException("API Key 不能为空");
        }
        if (value.length() > 4096) {
            throw new SecretException("API Key 过长");
        }
        String id = identifier(petId, origin);
        try {
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey(alias(id)));
            cipher.updateAAD(aad(petId, origin));
            byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            byte[] iv = cipher.getIV();
            ByteBuffer payload = ByteBuffer.allocate(2 + iv.length + encrypted.length);
            payload.put((byte) FORMAT_VERSION);
            payload.put((byte) iv.length);
            payload.put(iv);
            payload.put(encrypted);
            preferences.edit()
                    .putString(blobKey(id), Base64.encodeToString(payload.array(), Base64.NO_WRAP))
                    .apply();
        } catch (Exception error) {
            throw new SecretException("系统无法安全保存 API Key");
        }
    }

    String load(String petId, String origin) throws SecretException {
        String id = identifier(petId, origin);
        String encoded = preferences.getString(blobKey(id), "");
        if (encoded == null || encoded.isEmpty()) {
            return "";
        }
        try {
            ByteBuffer payload = ByteBuffer.wrap(Base64.decode(encoded, Base64.NO_WRAP));
            int version = payload.get() & 0xff;
            int ivLength = payload.get() & 0xff;
            if (version != FORMAT_VERSION || ivLength < 12 || ivLength > 32) {
                throw new SecretException("已保存的 API Key 格式无效");
            }
            if (payload.remaining() <= ivLength) {
                throw new SecretException("已保存的 API Key 数据不完整");
            }
            byte[] iv = new byte[ivLength];
            payload.get(iv);
            byte[] encrypted = new byte[payload.remaining()];
            payload.get(encrypted);

            KeyStore keyStore = KeyStore.getInstance(ANDROID_KEYSTORE);
            keyStore.load(null);
            SecretKey key = (SecretKey) keyStore.getKey(alias(id), null);
            if (key == null) {
                throw new SecretException("系统密钥已不存在，请重新填写 API Key");
            }
            Cipher cipher = Cipher.getInstance(TRANSFORMATION);
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_BITS, iv));
            cipher.updateAAD(aad(petId, origin));
            return new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8);
        } catch (SecretException error) {
            throw error;
        } catch (Exception error) {
            throw new SecretException("API Key 无法解密，请清除后重新填写");
        }
    }

    boolean has(String petId, String origin) throws SecretException {
        return !load(petId, origin).isEmpty();
    }

    void delete(String petId, String origin) {
        if (origin == null || origin.trim().isEmpty()) {
            return;
        }
        String id = identifier(petId, origin);
        preferences.edit().remove(blobKey(id)).apply();
        try {
            KeyStore keyStore = KeyStore.getInstance(ANDROID_KEYSTORE);
            keyStore.load(null);
            if (keyStore.containsAlias(alias(id))) {
                keyStore.deleteEntry(alias(id));
            }
        } catch (Exception ignored) {
            // 删除设置时不把系统 Keystore 的暂时性故障暴露为应用崩溃。
        }
    }

    private SecretKey getOrCreateKey(String alias) throws Exception {
        KeyStore keyStore = KeyStore.getInstance(ANDROID_KEYSTORE);
        keyStore.load(null);
        SecretKey existing = (SecretKey) keyStore.getKey(alias, null);
        if (existing != null) {
            return existing;
        }
        KeyGenerator generator = KeyGenerator.getInstance(
                KeyProperties.KEY_ALGORITHM_AES,
                ANDROID_KEYSTORE
        );
        generator.init(new KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setRandomizedEncryptionRequired(true)
                .build());
        return generator.generateKey();
    }

    private String alias(String id) {
        return context.getPackageName() + ".onepic.companion." + id;
    }

    private static String blobKey(String id) {
        return "secret." + id;
    }

    private static byte[] aad(String petId, String origin) {
        return (petId + "\n" + origin).getBytes(StandardCharsets.UTF_8);
    }

    private static String identifier(String petId, String origin) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] value = digest.digest(aad(petId, origin));
            StringBuilder builder = new StringBuilder();
            for (int index = 0; index < 16; index++) {
                builder.append(String.format("%02x", value[index]));
            }
            return builder.toString();
        } catch (Exception error) {
            throw new IllegalStateException("SHA-256 不可用", error);
        }
    }

    static final class SecretException extends Exception {
        SecretException(String message) {
            super(message);
        }
    }
}
