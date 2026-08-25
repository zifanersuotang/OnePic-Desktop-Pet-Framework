package com.onepic.desktop.pet;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;

/**
 * 读取由 prepare_android_pet.py 脱敏装配的宠物运行清单。
 *
 * <p>Android 端只认识 assets/pet 下的相对资源，不读取原图、候选、审批文件或本机路径。
 * 运行序列与养成参数来自桌面版已批准 manifest，因此不会重新解释或修改角色资产。</p>
 */
final class PetAssetManifest {
    static final String ASSET_ROOT = "pet/";

    static final class Sequence {
        final List<String> frames;
        final long frameDurationMs;
        final boolean loop;

        Sequence(List<String> frames, long frameDurationMs, boolean loop) {
            this.frames = Collections.unmodifiableList(new ArrayList<>(frames));
            this.frameDurationMs = Math.max(50L, frameDurationMs);
            this.loop = loop;
        }
    }

    static final class CareConfig {
        final boolean enabled;
        final int initialCoins;
        final int feedCost;
        final int feedAmount;
        final int drinkCost;
        final int drinkAmount;
        final int playVitalityCost;
        final int playHappinessGain;
        final int focusRewardCoins;
        final int focusRewardExperience;

        CareConfig(JSONObject value) {
            JSONObject source = value == null ? new JSONObject() : value;
            enabled = source.optBoolean("enabled", false);
            initialCoins = limited(source.optInt("initial_coins", 60), 0, 10000);
            feedCost = limited(source.optInt("feed_cost", 8), 0, 1000);
            feedAmount = limited(source.optInt("feed_amount", 24), 1, 100);
            drinkCost = limited(source.optInt("drink_cost", 2), 0, 1000);
            drinkAmount = limited(source.optInt("drink_amount", 28), 1, 100);
            playVitalityCost = limited(
                    source.optInt("play_vitality_cost", 6),
                    0,
                    100
            );
            playHappinessGain = limited(
                    source.optInt("play_happiness_gain", 14),
                    1,
                    100
            );
            focusRewardCoins = limited(
                    source.optInt("focus_reward_coins", 18),
                    0,
                    10000
            );
            focusRewardExperience = limited(
                    source.optInt("focus_reward_experience", 10),
                    0,
                    10000
            );
        }

        private static int limited(int value, int minimum, int maximum) {
            return Math.min(maximum, Math.max(minimum, value));
        }
    }

    private final String petId;
    private final String displayName;
    private final Map<String, Sequence> sequences;
    private final Map<String, List<String>> dialogue;
    private final CareConfig careConfig;
    private final List<String> interactions;
    private final List<String> companionActions;
    private final Random random = new Random();

    private PetAssetManifest(
            String petId,
            String displayName,
            Map<String, Sequence> sequences,
            Map<String, List<String>> dialogue,
            CareConfig careConfig
    ) {
        this.petId = petId;
        this.displayName = displayName;
        this.sequences = Collections.unmodifiableMap(new HashMap<>(sequences));
        this.dialogue = Collections.unmodifiableMap(new HashMap<>(dialogue));
        this.careConfig = careConfig;
        this.interactions = buildInteractionList(sequences);
        this.companionActions = buildCompanionActionList(sequences);
    }

    static PetAssetManifest load(Context context) throws IOException, JSONException {
        JSONObject root = new JSONObject(readAsset(context, ASSET_ROOT + "manifest.json"));
        String petId = root.optString("pet_id", "android-pet");
        if (!petId.matches("[a-z0-9][a-z0-9_-]*")) {
            throw new JSONException("运行清单 pet_id 无效");
        }
        String displayName = root.optString("display_name", "桌面宠物");
        Map<String, Sequence> sequences = new HashMap<>();

        JSONObject animations = root.getJSONObject("animations");
        JSONArray animationNames = animations.names();
        if (animationNames != null) {
            for (int index = 0; index < animationNames.length(); index++) {
                String name = animationNames.getString(index);
                List<String> frames = stringArray(animations.getJSONArray(name));
                sequences.put(
                        name,
                        new Sequence(frames, defaultDuration(name), isBaseLoop(name))
                );
            }
        }

        JSONObject extensions = root.optJSONObject("extension_animations");
        if (extensions != null) {
            JSONArray extensionNames = extensions.names();
            if (extensionNames != null) {
                for (int index = 0; index < extensionNames.length(); index++) {
                    String name = extensionNames.getString(index);
                    JSONObject definition = extensions.getJSONObject(name);
                    sequences.put(
                            name,
                            new Sequence(
                                    stringArray(definition.getJSONArray("frames")),
                                    definition.optLong("frame_duration_ms", 140L),
                                    definition.optBoolean("loop", false)
                            )
                    );
                }
            }
        }

        if (!sequences.containsKey("walk") || !sequences.containsKey("idle")) {
            throw new JSONException("运行清单必须包含 walk 和 idle 动画");
        }

        Map<String, List<String>> dialogue = new HashMap<>();
        JSONObject dialogueSystem = root.optJSONObject("dialogue_system");
        if (dialogueSystem != null && dialogueSystem.optBoolean("enabled", false)) {
            JSONObject lines = dialogueSystem.optJSONObject("lines");
            if (lines != null) {
                JSONArray lineNames = lines.names();
                if (lineNames != null) {
                    for (int index = 0; index < lineNames.length(); index++) {
                        String name = lineNames.getString(index);
                        dialogue.put(name, stringArray(lines.getJSONArray(name)));
                    }
                }
            }
        }
        CareConfig careConfig = new CareConfig(root.optJSONObject("care_simulation"));
        return new PetAssetManifest(petId, displayName, sequences, dialogue, careConfig);
    }

    String petId() {
        return petId;
    }

    String displayName() {
        return displayName;
    }

    CareConfig careConfig() {
        return careConfig;
    }

    Sequence sequence(String name) {
        Sequence value = sequences.get(name);
        return value != null ? value : sequences.get("idle");
    }

    boolean hasSequence(String name) {
        return sequences.containsKey(name);
    }

    List<String> companionActionNames() {
        return companionActions;
    }

    boolean isCompanionActionAllowed(String name) {
        return name != null && companionActions.contains(name);
    }

    String preferredSideWalkSequence() {
        if (sequences.containsKey("side_walk_v2")) {
            return "side_walk_v2";
        }
        if (sequences.containsKey("side_walk_v1")) {
            return "side_walk_v1";
        }
        List<String> names = new ArrayList<>(sequences.keySet());
        Collections.sort(names);
        for (String name : names) {
            if (name.startsWith("side_walk")) {
                return name;
            }
        }
        return "";
    }

    String randomInteraction() {
        if (interactions.isEmpty()) {
            return "wave";
        }
        return interactions.get(random.nextInt(interactions.size()));
    }

    String randomDialogue(String action) {
        List<String> values = dialogue.get(action);
        if ((values == null || values.isEmpty()) && action.contains("_")) {
            values = dialogue.get(action.substring(0, action.indexOf('_')));
        }
        if (values == null || values.isEmpty()) {
            values = dialogue.get("idle");
        }
        if (values == null || values.isEmpty()) {
            return "";
        }
        return values.get(random.nextInt(values.size()));
    }

    private static List<String> buildInteractionList(Map<String, Sequence> sequences) {
        String[] preferred = {
                "signature_wave",
                "jump",
                "micro_expressions_v1",
                "wait_response",
                "wave",
                "happy",
                "shy",
                "surprised",
                "curious"
        };
        List<String> result = new ArrayList<>();
        for (String name : preferred) {
            if (sequences.containsKey(name)) {
                result.add(name);
            }
        }
        return Collections.unmodifiableList(result);
    }

    private static List<String> buildCompanionActionList(Map<String, Sequence> sequences) {
        String[] whitelist = {
                "happy",
                "shy",
                "surprised",
                "curious",
                "wave",
                "signature_wave",
                "jump",
                "wait_response",
                "micro_expressions_v1",
                "focus_work",
                "idle"
        };
        List<String> result = new ArrayList<>();
        for (String name : whitelist) {
            if (sequences.containsKey(name)) {
                result.add(name);
            }
        }
        return Collections.unmodifiableList(result);
    }

    private static boolean isBaseLoop(String name) {
        return "walk".equals(name) || "idle".equals(name) || "sleep".equals(name);
    }

    private static long defaultDuration(String name) {
        if ("walk".equals(name)) {
            return 110L;
        }
        if ("drag".equals(name)) {
            return 130L;
        }
        if ("idle".equals(name)) {
            return 360L;
        }
        if ("sit".equals(name) || "sleep".equals(name)) {
            return 420L;
        }
        return 520L;
    }

    private static List<String> stringArray(JSONArray array) throws JSONException {
        List<String> result = new ArrayList<>();
        for (int index = 0; index < array.length(); index++) {
            String path = array.getString(index);
            if (path.startsWith("/") || path.startsWith("\\") || path.contains("..")) {
                throw new JSONException("非法宠物资源路径：" + path);
            }
            result.add(path.replace('\\', '/'));
        }
        if (result.isEmpty()) {
            throw new JSONException("动画序列不能为空");
        }
        return result;
    }

    private static String readAsset(Context context, String path) throws IOException {
        StringBuilder builder = new StringBuilder();
        try (
                InputStream input = context.getAssets().open(path);
                BufferedReader reader = new BufferedReader(
                        new InputStreamReader(input, StandardCharsets.UTF_8)
                )
        ) {
            String line;
            while ((line = reader.readLine()) != null) {
                builder.append(line).append('\n');
            }
        }
        return builder.toString();
    }
}
