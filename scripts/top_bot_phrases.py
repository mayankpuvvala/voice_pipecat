"""How repeatable is the bot's speech? Decides whether a TTS phrase cache is worth it.

Reads the Recordings tab (read-only), takes the bot's lines from each
transcript, splits them into sentences, and reports how much of all spoken
text (characters = what TTS bills) the top-N most frequent sentences cover.

    python scripts/top_bot_phrases.py [--top 40] [--bot Anil]

If the top 40 cover, say, 5% of characters, a replay cache for them is not
worth its complexity; if it's 40%+, it is. Counts digits/names as-is after
light normalisation, so "table for 4" and "table for 5" stay separate -- the
realistic exact-match hit rate.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config.restaurants import ACTIVE_RESTAURANT  # noqa: E402
from app.services import sheets_client  # noqa: E402

_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _normalize(sentence: str) -> str:
    return re.sub(r"\s+", " ", sentence).strip().lower().rstrip(".!?")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--bot", default=ACTIVE_RESTAURANT.bot_name, help="speaker label in transcripts")
    args = parser.parse_args()

    counts: Counter[str] = Counter()
    chars: Counter[str] = Counter()
    for row in sheets_client.read_rows("Recordings"):
        for line in str(row.get("Transcript", "")).splitlines():
            if not line.startswith(f"{args.bot}:"):
                continue
            for sentence in _SENTENCE.split(line[len(args.bot) + 1 :].strip()):
                key = _normalize(sentence)
                if key:
                    counts[key] += 1
                    chars[key] += len(key)

    total_sentences, total_chars = sum(counts.values()), sum(chars.values())
    if not total_sentences:
        print("No bot lines found in Recordings.")
        return
    top = counts.most_common(args.top)
    top_chars = sum(chars[k] for k, _ in top)
    print(f"{total_sentences} bot sentences, {total_chars} characters, {len(counts)} distinct")
    print(f"Top {args.top} cover {top_chars / total_chars:.1%} of characters, "
          f"{sum(n for _, n in top) / total_sentences:.1%} of sentences\n")
    for key, n in top:
        print(f"{n:5d}  {key[:100]}")


if __name__ == "__main__":
    main()
