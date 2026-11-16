"""Poses you can ask her for, by typing or saying it ("sit down", "lie down", "bow", "hands up"...).

`parse_command(text)` turns a short request into an action name. Held poses stay until you say
"stand up" / "relax" (or after a couple of minutes); one-shot actions (jump, wave, twirl...) just play.
Gemini can also choose a pose by adding a [pose:NAME] tag to a reply.
"""
from __future__ import annotations

import re

# held body poses (drawn by the 3D renderers; "sit" / "lie" work on every character)
HELD = ("sit", "lie", "kneel", "squat", "bow", "hands_up", "salute", "peace", "think", "arms_crossed",
        "hands_on_hips", "t_pose", "dance", "hug_self", "shy")
# one-shot actions (CharacterWindow.do_cute and friends)
ONESHOT = ("stand", "jump", "hop_hop", "twirl", "nya", "heart", "kiss", "wave", "turn_around", "face_me", "sleep",
           "sing", "hum", "plant", "plant_trees")
ALL = HELD + ONESHOT

PATTERNS: list[tuple[str, str]] = [
    ("stand", r"\b(stand( up)?|get up|relax|normal pose|at ease|stop posing)\b"),
    ("lie", r"\b(lie|lay|laying|lying) ?(down|on the (floor|ground))?\b"),
    ("sit", r"\b(sit|sitting|have a seat|take a seat)\b"),
    ("kneel", r"\b(kneel|seiza|on your knees)\b"),
    ("squat", r"\b(squat|crouch)\b"),
    ("bow", r"\b(bow|ojigi)\b"),
    ("hands_up", r"\b(hands up|arms up|raise (your )?(hands|arms)|banzai|hooray)\b"),
    ("salute", r"\bsalute\b"),
    ("peace", r"\b(peace sign|v ?sign|peace)\b"),
    ("think", r"\b(think(ing)? pose|hand on (your )?chin)\b"),
    ("arms_crossed", r"\b(cross (your )?arms|arms crossed|fold (your )?arms)\b"),
    ("hands_on_hips", r"\b(hands on (your )?hips|hero pose|confident pose)\b"),
    ("t_pose", r"\bt[- ]?pose\b"),
    ("dance", r"\b(dance|dancing|boogie)\b"),
    ("hug_self", r"\b(hug yourself|self hug|you cold)\b"),
    ("shy", r"\b(be shy|shy pose|cute pose)\b"),
    ("plant_trees", r"\b(plant (some |a |the )?trees?|grow (some )?trees?)\b"),
    ("plant", r"\b(plant (a |some )?(flowers?|seeds?)|garden(ing)?)\b"),
    ("hum", r"\b(hum|humming)\b"),
    ("sing", r"\b(sing|singing)\b"),
    ("jump", r"\b(jump|leap)\b"),
    ("hop_hop", r"\b(hop|bunny hop)\b"),
    ("twirl", r"\b(twirl|spin( around)?)\b"),
    ("turn_around", r"\b(turn around|turn back|show me your back)\b"),
    ("face_me", r"\b(face me|turn to me|look at me)\b"),
    ("nya", r"\b(nya|cat pose|meow|be a cat)\b"),
    ("heart", r"\b(heart|make a heart)\b"),
    ("kiss", r"\b(blow (me )?a kiss|kiss)\b"),
    ("wave", r"\b(wave|say (hi|hello|bye))\b"),
]
COMMAND_HINT = re.compile(r"^(please |pls |can you |could you |will you |now |ok |okay |hey |\w+[, ] )*", re.I)
_POSE_TAG = re.compile(r"\[pose:(\w+)\]", re.I)


def parse_command(text: str) -> str | None:
    """A short request like 'sit down please' / 'can you bow?' -> 'sit' / 'bow'. Longer chat -> None."""
    low = text.lower().strip()
    words = re.findall(r"[a-z']+", low)
    if not words or len(words) > 9 or "?" in low and not re.match(r"^(can|could|will|would) you", low):
        return None
    if re.search(r"\b(don't|do not|stop|never|why|how|when|what)\b", low) and not re.search(r"stop posing", low):
        return None
    for name, pat in PATTERNS:
        if re.search(pat, low):
            return name
    return None


def pop_pose_tag(text: str) -> tuple[str, str | None]:
    """Gemini reply with an optional [pose:NAME] tag -> (text without tag, pose or None)."""
    m = _POSE_TAG.search(text)
    if not m:
        return text, None
    name = m.group(1).lower()
    return (text[:m.start()] + text[m.end():]).strip(), (name if name in ALL else None)
