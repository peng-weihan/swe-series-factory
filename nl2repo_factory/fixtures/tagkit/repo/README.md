# TagKit fixture

This synthetic reference project implements the four tests in section 15 of
`nl2repo.md`. `normalize_tags(text)` splits strings on commas, strips whitespace,
casefolds, removes empty tokens and deduplicates while preserving first order.
Non-strings raise `InvalidTagInput`, a subclass of `TypeError`.
