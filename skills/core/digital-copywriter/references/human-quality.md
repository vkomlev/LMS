# Human Quality Check

Use before returning publishable text.

## Hard Fails

- Fake facts, numbers, prices, cases, scarcity, or deadlines.
- More than one obvious AI wrapper such as "важно отметить", "стоит подчеркнуть", "в заключение".
- Empty epithets without evidence: "уникальный", "эффективный", "инновационный", "качественный".
- Canned social phrasing: "это про", "не про X, а про Y", "случился инсайт", "вселенная подкинула".
- Jargon pile-up for a non-expert audience.

## Rhythm

- Mix short and medium sentences.
- Keep at least a few sentences under 7 words in social posts.
- Use lists only when they make scanning easier, not as the default shape.
- Do not overuse dashes.

## Evidence

- Every claim should be grounded in supplied context, local references, or marked `[уточнить]`.
- For technical topics aimed at beginners, explain through a concrete everyday analogy before jargon.

## Verdict

Return `PASS` only when the text is publishable without sounding like a generic AI answer.

