"""A small, fixed test set with answers that can be checked automatically.

It is a quick sanity check of how capability changes with model size, not a published benchmark.
Each item asks for a short answer; most end with a line "ANSWER: <answer>" so checking is reliable.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

ANSWER_RULE = "Think it through briefly, then finish with a final line in the form 'ANSWER: <answer>'."


@dataclass(frozen=True)
class Item:
    id: str
    category: str
    prompt: str
    check: Callable[[str], bool]
    expected: str
    answer_line: bool = True  # False: the whole reply is checked (format-following items)


def final_answer(text: str) -> str:
    hits = re.findall(r"ANSWER\s*[:：]\s*(.+)", text, flags=re.I)
    ans = hits[-1] if hits else text.strip().splitlines()[-1] if text.strip() else ""
    return ans.strip().strip("*`_ ").rstrip(".").strip()


def _num(expected: float, tol: float = 1e-6) -> Callable[[str], bool]:
    def check(ans: str) -> bool:
        nums = re.findall(r"-?\d[\d,]*\.?\d*", ans)
        for n in nums:
            try:
                v = float(n.replace(",", ""))
            except ValueError:
                continue
            if abs(v - expected) <= max(tol, abs(expected) * tol):
                return True
        return False
    return check


def _word(*accepted: str) -> Callable[[str], bool]:
    acc = [a.lower() for a in accepted]
    return lambda ans: any(re.search(rf"\b{re.escape(a)}\b", ans.lower()) for a in acc)


def _strip_fences(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    return re.sub(r"\s*```$", "", t).strip()


def _json_person(text: str) -> bool:
    try:
        obj = json.loads(_strip_fences(text))
    except json.JSONDecodeError:
        return False
    return (isinstance(obj, dict) and set(obj) == {"name", "age"} and isinstance(obj["name"], str)
            and isinstance(obj["age"], int) and not isinstance(obj["age"], bool))


def _three_lower_words(text: str) -> bool:
    words = re.sub(r"[.!,]", "", text.strip()).split()
    return len(words) == 3 and all(w.isalpha() and w == w.lower() for w in words)


def _numbered_five(text: str) -> bool:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return len(lines) == 5 and all(re.match(r"^\s*\d+[.)]\s+\S", ln) for ln in lines)


def _banana(text: str) -> bool:
    return re.sub(r"[^\w]", "", text) == "BANANA"


ITEMS: list[Item] = [
    # Arithmetic and word problems
    Item("math-mul", "math", "What is 17 × 24?", _num(408), "408"),
    Item("math-pens", "math", "Pens cost $3 each, or $10 for a pack of 4. What is the lowest cost, in dollars, "
         "to buy exactly 10 pens?", _num(26), "26"),
    Item("math-time", "math", "A train leaves at 14:45 and the trip takes 2 hours 50 minutes. At what time does it "
         "arrive? Give the time as HH:MM in 24-hour format.", lambda a: "17:35" in a, "17:35"),
    Item("math-pct", "math", "What is 15% of 240?", _num(36), "36"),
    Item("math-even", "math", "What is the sum of all even numbers from 2 to 50 inclusive?", _num(650), "650"),
    Item("math-age", "math", "Alice is three times as old as Bob. In 10 years Alice will be twice as old as Bob. "
         "How old is Alice now?", _num(30), "30"),
    # Reasoning
    Item("reason-syllogism", "reasoning", "All bloops are razzies and all razzies are lazzies. Are all bloops "
         "definitely lazzies? Answer yes or no.", _word("yes"), "yes"),
    Item("reason-apples", "reasoning", "I have 3 apples. I eat one, buy 4 more, then give away half of what I "
         "have. How many apples do I have now?", _num(3), "3"),
    Item("reason-order", "reasoning", "Tom is taller than Sam. Sam is taller than Ann. Ann is taller than Joe. "
         "Who is the second shortest?", _word("ann"), "Ann"),
    Item("reason-letters", "reasoning", "How many times does the letter 'r' appear in the word 'strawberry'?",
         _num(3), "3"),
    Item("reason-days", "reasoning", "What day of the week is 10 days after a Wednesday?", _word("saturday"),
         "Saturday"),
    Item("reason-batball", "reasoning", "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than the "
         "ball. How many cents does the ball cost?", _num(5), "5"),
    # Knowledge
    Item("know-gold", "knowledge", "What is the chemical symbol for gold?", lambda a: re.search(r"\bAu\b", a) is not None, "Au"),
    Item("know-aus", "knowledge", "What is the capital city of Australia?", _word("canberra"), "Canberra"),
    Item("know-austen", "knowledge", "Who wrote the novel 'Pride and Prejudice'?", _word("austen"), "Jane Austen"),
    Item("know-wall", "knowledge", "In what year did the Berlin Wall fall?", _num(1989), "1989"),
    Item("know-planet", "knowledge", "Which planet in our solar system has the most mass?", _word("jupiter"), "Jupiter"),
    # Reading code (no code is executed)
    Item("code-squares", "code", "What does this Python print?\n\nprint(sum(x * x for x in range(5)))", _num(30), "30"),
    Item("code-slice", "code", "What does this Python print?\n\ns = 'hello'\nprint(s[::-1][1:3])",
         lambda a: re.sub(r"[\"'`]", "", a).strip() == "ll", "ll"),
    Item("code-alias", "code", "What does this Python print?\n\na = [1, 2, 3]\nb = a\nb.append(4)\nprint(len(a))",
         _num(4), "4"),
    Item("code-set", "code", "What does this Python print?\n\nprint(len({1, 2, 2, 3, 3, 3}))", _num(3), "3"),
    Item("code-fact", "code", "What does this Python print?\n\ndef f(n):\n    return 1 if n < 2 else n * f(n - 1)\n"
         "print(f(5))", _num(120), "120"),
    # Following format instructions (the whole reply is checked)
    Item("fmt-json", "instructions", "Reply with valid JSON only, no other text: an object with exactly two keys, "
         "\"name\" (a string) and \"age\" (an integer), describing a made-up person.", _json_person,
         '{"name": "...", "age": 42}', answer_line=False),
    Item("fmt-three", "instructions", "Reply with exactly three words, all lowercase, and nothing else.",
         _three_lower_words, "three lowercase words", answer_line=False),
    Item("fmt-list", "instructions", "List five fruits as a numbered list (1. to 5.), one per line, with no other "
         "text before or after.", _numbered_five, "5 numbered lines", answer_line=False),
    Item("fmt-caps", "instructions", "Reply with the word banana in all capital letters and nothing else.",
         _banana, "BANANA", answer_line=False),
]

CATEGORIES = sorted({i.category for i in ITEMS})


def build_prompt(item: Item) -> str:
    return f"{item.prompt}\n\n{ANSWER_RULE}" if item.answer_line else item.prompt


def grade(item: Item, reply: str) -> tuple[bool, str]:
    ans = final_answer(reply) if item.answer_line else reply.strip()
    return item.check(ans), ans
