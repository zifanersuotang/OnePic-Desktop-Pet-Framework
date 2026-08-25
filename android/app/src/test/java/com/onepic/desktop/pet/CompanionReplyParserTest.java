package com.onepic.desktop.pet;

import static org.junit.Assert.assertEquals;

import java.util.Arrays;

import org.junit.Test;

/** 验证模型只能触发当前运行包明确允许的动作。 */
public final class CompanionReplyParserTest {
    @Test
    public void extractsAllowedActionAndHidesProtocolTag() {
        CompanionReplyParser.ParsedReply reply = CompanionReplyParser.parse(
                "今天也一起加油吧～ [[action:happy]]",
                Arrays.asList("happy", "wave")
        );

        assertEquals("今天也一起加油吧～", reply.text);
        assertEquals("happy", reply.action);
    }

    @Test
    public void dropsUnknownActionWithoutExecutingIt() {
        CompanionReplyParser.ParsedReply reply = CompanionReplyParser.parse(
                "我会陪着你。[[action:open_url]]",
                Arrays.asList("happy", "wave")
        );

        assertEquals("我会陪着你。", reply.text);
        assertEquals("", reply.action);
    }

    @Test
    public void onlyUsesFirstAllowedAction() {
        CompanionReplyParser.ParsedReply reply = CompanionReplyParser.parse(
                "先挥手，再开心。[[action:open_url]][[action:WAVE]][[action:happy]]",
                Arrays.asList("happy", "wave")
        );

        assertEquals("先挥手，再开心。", reply.text);
        assertEquals("wave", reply.action);
    }

    @Test
    public void bubbleTextIsSingleLineAndBounded() {
        assertEquals(
                "第一行 第二行",
                CompanionReplyParser.bubbleText("第一行\n第二行")
        );
        assertEquals(
                26,
                CompanionReplyParser.bubbleText(
                        "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"
                ).length()
        );
        String emojiBubble = CompanionReplyParser.bubbleText(
                "😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀😀"
        );
        assertEquals(26, emojiBubble.codePointCount(0, emojiBubble.length()));
    }
}
