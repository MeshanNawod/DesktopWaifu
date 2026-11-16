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
    "hair_tug": ["Ouch! Don't pull my hair~!", "Hey... that's attached to my head, you know. Hmph.",
                 "Mou~ stop pulling! It took forever to brush!"],
    "eyes_poke": ["Eek! Careful, that's my eye!", "Waah! Don't poke my eyes, you'll make me blind!",
                  "My eye! ...Hey, I'm warning you~"],
    "nose_poke": ["Boop?! Hey, my nose isn't a button!", "Achoo-- I mean, don't poke my nose!",
                  "Eep! Watch it, that's my nose!"],
    "sensitive_stroke": ["...Ehehe. You're being so gentle... I-it's okay.", "Mmh... if it's you, I don't mind.",
                         "S-so warm... don't tell anyone, okay?"],
    "sensitive_scold": ["Kyaa! W-what do you think you're doing?! Pervert!", "H-hands off! My face is on fire!!",
                        "Baka! Don't touch me there so suddenly!", "Eeeek! I'm telling everyone about this!"],
    "broke_free": ["Hah! I wriggled free!", "Hmph, you can't hold me forever!", "Freedom! ...Ow, my landing."],
    "scarf_tug": ["Don't pull it, it'll come undone!", "My scarf!"],
    # teasing / sensitive touches (flustered, depending on how close you are)
    "sensitive_defensive": ["H-hey! Hands off!", "What do you think you're doing?!", "We're NOT that close yet!"],
    "sensitive_shy": ["W-wait, that's embarrassing...", "Eek! D-don't touch there so suddenly...",
                      "My face is burning up..."],
    "sensitive_playful": ["Mou~ you're such a tease!", "Hehe... you're bold today, aren't you?",
                          "Behave~ ...or don't. Ehehe."],
    "playful": ["Boop me one more time, I dare you~", "Hehe, are you trying to tickle me?"],
    # petting (double click / stroke)
    "head_pet": ["Ehehe~ more headpats please...", "Mmm... I could melt like this.", "You're so gentle with me~"],
    "hand_pet": ["Your hand is so warm... let's stay like this.", "D-don't let go, okay?"],
    "cheek_pet": ["Ehehe... my cheeks are burning now.", "So soft... that's nice."],
    "mouth_pet": ["Mmh?! W-what are you doing to my lips...", "S-so bold..."],
    "eyes_pet": ["Hehe, peekaboo~", "I can't see you like that~"],
    "hair_pet": ["Do you like my hair? Ehehe~", "That feels nice..."],
    "body_pet": ["Hehe, that tickles!", "You're so sweet today."],
    "legs_pet": ["Eek! Ticklish!", "Hey~ that tickles my legs!"],
    # legs
    "legs_gentle": ["Hm? My legs?", "Hehe, that tickles!"],
    "legs_rough": ["Ow! My leg!", "Hey, that hurts!"],
    # lifted / struggling
    "struggle_hand": ["Waah! Don't lift me by my arm!", "Put me down~! I'm dangling!", "Kyaa! My arm!"],
    "struggle_leg": ["Kyaaa! I'm upside down! Put me down!", "My skirt-- I mean, PUT ME DOWN!", "The world is upside down!"],
    "struggle_body": ["Whoa! Put me down~!", "Wheee! ...No wait, put me down!", "H-hey, careful!"],
    "put_down": ["Phew... solid ground.", "Don't do that so suddenly!", "My head is spinning..."],
    # squeezing (press and hold)
    "head_squeeze": ["Mmmh~ are you squishing my head?", "Ehehe, my head isn't a stress ball!"],
    "cheek_squeeze": ["Puni puni... m-my cheeks!", "Mmph~ don't squish my cheeks, I'll look silly!",
                      "Fwuh?! My cheeks are all squishy now~"],
    "body_squeeze": ["Kyu~! That's a tight hug!", "Ehehe~ squeezy hug!", "Mmh... warm..."],
    "hand_squeeze": ["Ehehe, you're squeezing my hand~", "Your hand is so warm... *squeeze back*"],
    "nose_squeeze": ["Hmmph! I can't breathe~!", "My nose! Ehehe, stop~"],
    "eyes_squeeze": ["Waah, I can't see! Ehehe~", "Hey~ let me see!"],
    "mouth_squeeze": ["Mmph! Mmmph mmph!", "Mmh?! I can't talk like this~"],
    "legs_squeeze": ["Eek! That tickles!", "Ehehe, my leg is ticklish~"],
    "hair_squeeze": ["Hehe, you like my hair?", "Careful, you'll mess it up~"],
    "too_hard": ["Ow ow ow! Too hard, too hard!", "Itai! Not so tight!", "Owie... you squeezed too hard!"],
    # moving her parts (drag)
    "head_move": ["Whoa~ where are you turning my head?", "Ehehe, my neck doesn't bend like that~",
                  "Hmm? You want me to look over there?"],
    "hand_move": ["Where are we going? Ehehe~", "Are you going to dance with me?", "My arm is following you~"],
    "legs_move": ["H-hey! That's my leg!", "Eek! Don't lift my leg so high!", "W-what kind of pose is this?!"],
    "body_move": ["Whoa, whoa! Don't push me~", "I'm falling over~!", "Hehe, I'm wobbly!"],
    "eyes_move": ["Ehehe, you're turning my face~"],
    "cheek_move": ["Mmh? You're stretching my face~"],
    "cute_jump": ["Yay~!", "Boing!", "Wheee~!"],
    "cute_hop_hop": ["Hop, hop~!", "Bunny hop~!"],
    "cute_twirl": ["Ta-da~ ♪", "Spin spin~!", "Look how cute my twirl is~"],
    "cute_nya": ["Nya~ ♡", "Nyaa~ pet me?", "Nya nya~!"],
    "cute_heart": ["Love you~ ♡", "Here, my heart~!", "Ehehe, a big heart for you!"],
    "cute_kiss": ["Chu~ ♡", "Mwah~!", "Catch it~! ♡"],
    "cute_wave": ["Hi hi~!", "Heyyy~!", "*waves* Ehehe"],
    "sit_down": ["Okay~ I'll sit here.", "Sitting down~", "Ahh, comfy."],
    "stand_up": ["Up I go~!", "Alright, standing!", "Hup!"],
    "turned": ["Hm? Where are you turning me?", "Wheee~ I'm spinning!", "Ehehe, I can see the other side now."],
    "knocked": ["Waaah--! *thud*", "Kyaa! I fell over!", "Owww... the world is spinning...", "@_@ ...so many stars..."],
    "get_up": ["Hmph! That was mean!", "*wobble* ...I'm okay. I think.", "Don't push me so hard, baka!",
               "Ugh... my head. Be gentler next time, okay?"],
    "attention": ["Psst~ hey! Over here!", "Heyyy, look at me~!", "Are you ignoring me? Hmph!",
                  "Notice me~! ...Please?"],
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
