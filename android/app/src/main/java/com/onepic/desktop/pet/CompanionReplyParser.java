package com.onepic.desktop.pet;

import java.util.Collection;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 从模型纯文本回复中提取并二次校验可选动作提示。 */
final class CompanionReplyParser {
    private static final Pattern ACTION_TAG = Pattern.compile(
            "\\[\\[action:([a-zA-Z0-9_-]{1,64})]]"
    );

    private CompanionReplyParser() {
    }

    static ParsedReply parse(String rawText, Collection<String> allowedActions) {
        String source = rawText == null ? "" : rawText.trim();
        Set<String> allowed = new HashSet<>();
        if (allowedActions != null) {
            for (String action : allowedActions) {
                if (action != null) {
                    allowed.add(action.toLowerCase(Locale.ROOT));
                }
            }
        }

        String selectedAction = "";
        Matcher matcher = ACTION_TAG.matcher(source);
        while (matcher.find()) {
            String candidate = matcher.group(1).toLowerCase(Locale.ROOT);
            if (selectedAction.isEmpty() && allowed.contains(candidate)) {
                selectedAction = candidate;
            }
        }
        String visibleText = matcher.replaceAll("").trim();
        return new ParsedReply(visibleText, selectedAction);
    }

    static String bubbleText(String text) {
        String compact = text == null ? "" : text
                .replace('\r', ' ')
                .replace('\n', ' ')
                .replaceAll("\\s+", " ")
                .trim();
        int maximum = 26;
        int codePoints = compact.codePointCount(0, compact.length());
        if (codePoints <= maximum) {
            return compact;
        }
        int end = compact.offsetByCodePoints(0, maximum - 1);
        return compact.substring(0, end) + "…";
    }

    static final class ParsedReply {
        final String text;
        final String action;

        ParsedReply(String text, String action) {
            this.text = text;
            this.action = action;
        }
    }
}
