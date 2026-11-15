"""Instant voice lines (spoken by TTS) for touches and state changes. Keys come from EmotionEngine."""
from __future__ import annotations

import random

LINES: dict[str, list[str]] = {
    # gentle / loving
    "head_gentle": ["Ehehe~ that feels nice...", "Mmm, more headpats please~", "You're spoiling me..."],
    "head_tap": ["Hehe, boop!", "Hm? You called?", "Pat pat~"],
    "hand_gentle": ["Y-you want to hold my hand...?", "Your hand is so warm...", "Let's stay like this a bit."],
    "hand_tap": ["Hi there~", "Hehe, high five!", "Did you need my hand?"],
    "mouth_gentle": ["Mmh? Are you shushing me?", "W-what are you doing to my lips?!", "Hehe, that tickles."],
    "mouth_tap": ["Mm? Something on my mouth?", "Boop on the lips... how sneaky."],
    "eyes_gentle": ["Hey, I can't see like that~", "Peekaboo?"],
    "eyes_tap": ["Hey! Careful with my eyes!", "Eek, don't poke there!"],
    "cheek_gentle": ["Ehehe... my cheeks are getting warm.", "Squishy, right?", "That's kind of nice..."],
    "cheek_tap": ["Poke! Hehe.", "Mmh? Is there something on my face?"],
    "hair_gentle": ["Do you like my hair? ...Thanks.", "Hehe, that tickles my neck."],
    "hair_tap": ["My hair? It took forever this morning!"],
    "body_gentle": ["Hehe, that tickles!", "Need something?"],
    "body_tap": ["Boop!", "Hm?", "Eep! Hi!"],
    "scarf_gentle": ["Careful with my scarf, it's precious to me."],
    "scarf_tap": ["My scarf! Don't mess it up~"],
    # rough / hurting
    "head_rough": ["Ow! That hurt!", "Itai! Why did you hit me?!", "Owwie... my head..."],
    "hand_rough": ["Ow! My hand!", "Hey! That stings!", "Ouch... too rough!"],
    "mouth_rough": ["Mmph! Ow! My lip!", "Ow, that hurts!", "Hey! Not my mouth!"],
    "eyes_rough": ["Ow ow ow! My eye!", "Aah! I can't see! That really hurt!", "Not my eyes, you meanie!"],
    "cheek_rough": ["Ow! Did you just slap me?!", "Ouch... my cheek is stinging."],
    "hair_rough": ["Ow! Don't pull my hair!", "Itai! That's attached to my head!"],
    "body_rough": ["Ow! What was that for?!", "Hey! That hurt!", "Owie..."],
    "scarf_rough": ["Hey! You'll choke me!"],
    "hurt_repeat": ["Stop it! I said it hurts...", "Why are you being so mean to me...?", "I'm not talking to you anymore. Hmph."],
    "skirt_rough": ["H-hands off my skirt!", "What do you think you're doing?!", "Hey! Behave yourself!"],
    "chest_rough": ["Kyaa! P-pervert!", "Where do you think you're touching?!", "One more time and I'm biting you!"],
    "hair_tug": ["Ah, don't pull my hair!", "Hey, that's attached!"],
    "scarf_tug": ["Don't pull it, it'll come undone!", "My scarf!"],
    # teasing / sensitive touches (flustered, depending on how close you are)
    "sensitive_defensive": ["H-hey! Hands off!", "What do you think you're doing?!", "We're NOT that close yet!"],
    "sensitive_shy": ["W-wait, that's embarrassing...", "Eek! D-don't touch there so suddenly...",
                      "My face is burning up..."],
    "sensitive_playful": ["Mou~ you're such a tease!", "Hehe... you're bold today, aren't you?",
                          "Behave~ ...or don't. Ehehe."],
    "playful": ["Boop me one more time, I dare you~", "Hehe, are you trying to tickle me?"],
    # state changes
    "sulk_comforted": ["...Hmph. That's not enough to make up for it.", "...I'm still a little mad.", "Sniff... okay, maybe a little better."],
    "forgive": ["...Okay. I forgive you.", "Fine... but be gentle next time, okay?", "Hmph. Apology accepted~"],
    "happy_burst": ["I'm so happy right now!", "Ehehe, I really like you, you know?", "Today is the best day!"],
    "wake_gentle": ["Mmm... good morning~", "Huh? Oh, it's you... *yawn*", "Five more minutes... okay, I'm up."],
    "wake_rough": ["Waah! I'm up, I'm up! So mean...", "Hey! That's no way to wake someone up!"],
    "wake_voice": ["Hm? You called me? I'm awake!", "*yawn* Mmh, I heard you~"],
    "fall_asleep": ["*yawn* I'm getting sleepy...", "I'll just rest my eyes for a bit...", "Good night~"],
    "pickup": ["Whoa! Put me down~!", "Wheee!", "H-hey, careful!"],
    "drop_hard": ["Ow ow ow... be gentle!", "Oof! That was NOT a soft landing!"],
    "welcome_back": ["Welcome back! I missed you.", "Oh! You're back~", "There you are!"],
    "user_left": ["Hm? Where did you go?", "Leaving me alone...?"],
    "music_start": ["Ooh, music! Let's listen together~", "I love this kind of vibe!", "Music time!"],
    "music_stop": ["Aww, the music stopped.", "That was nice."],
    "learned": ["I'll remember that!", "Noted~ I won't forget.", "Got it, saved in my heart."],
}


def line(key: str) -> str:
    if key in LINES:
        return random.choice(LINES[key])
    base = key.split("_")[0]
    for k in (f"{base}_gentle", "body_tap"):
        if k in LINES:
            return random.choice(LINES[k])
    return "Hm?"
