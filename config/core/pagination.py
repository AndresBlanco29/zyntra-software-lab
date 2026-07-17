QUICK_JUMP_PAGE_STEP = 5
QUICK_JUMP_PAGE_COUNT = 4
QUICK_JUMP_MIN_START = 10
VISIBLE_PAGE_WINDOW = 5


def get_quick_jump_pages(current_page, total_pages, *, step=QUICK_JUMP_PAGE_STEP, count=QUICK_JUMP_PAGE_COUNT):
    """Return up to four forward page numbers spaced by five (e.g. 10, 15, 20, 25)."""
    if total_pages <= 1 or current_page >= total_pages:
        return []

    start_page = max(
        QUICK_JUMP_MIN_START,
        ((max(current_page, 1) - 1) // step + 2) * step,
    )

    jump_pages = []
    page_number = start_page
    while len(jump_pages) < count and page_number <= total_pages:
        if page_number > current_page:
            jump_pages.append(page_number)
        page_number += step
    return jump_pages


def get_visible_page_numbers(current_page, total_pages, *, window=VISIBLE_PAGE_WINDOW):
    """Return a consecutive page window centered on the current page (KOS-style)."""
    if total_pages <= 1:
        return []

    current = max(1, min(int(current_page or 1), int(total_pages)))
    total = int(total_pages)
    size = max(1, int(window or VISIBLE_PAGE_WINDOW))

    if total <= size:
        return list(range(1, total + 1))

    half = size // 2
    start = max(1, current - half)
    end = min(total, start + size - 1)
    start = max(1, end - size + 1)
    return list(range(start, end + 1))
