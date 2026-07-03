def normalize_stored_phone_number(value):
    """Return exactly 10 US digits for storage, or an empty string when invalid."""
    digits = ''.join(character for character in str(value or '') if character.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    if len(digits) == 10:
        return digits
    return ''
