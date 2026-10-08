def _tokens(text):
    return [part.strip().casefold() for part in text.split(",") if part.strip()]
