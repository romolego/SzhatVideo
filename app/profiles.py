"""Размеры просмотра без увеличения исходника; VMAF не является процентом качества."""

def fit_height(media, width, height):
    factor = min(1.0, width / media.width, height / media.height)
    return max(2, int(media.height * factor) // 2 * 2)

def profiles_for(media, screen_size, threshold):
    sw, sh = screen_size
    phone = (1280, 720) if media.width >= media.height else (720, 1280)
    return [
        dict(key='maximum', name='Максимум качества', height=media.height // 2 * 2,
             threshold=threshold, first=True,
             detail='Исходное разрешение; самый качественный из проверенных вариантов с уменьшением размера ≥ 3%.'),
        dict(key='computer', name='Для вашего экрана', height=fit_height(media, sw, sh),
             threshold=threshold, first=False,
             detail=f'Экран {sw}×{sh}: максимальное сжатие среди проверенных вариантов при VMAF ≥ {threshold:g} в размере просмотра.'),
        dict(key='phone', name='Для телефона', height=fit_height(media, *phone),
             threshold=max(90.0, threshold-2), first=False,
             detail=f'Ориентир {phone[0]}×{phone[1]}, без увеличения исходника. VMAF ≥ {max(90.0, threshold-2):g} в размере просмотра; модель телефона не определяется.'),
    ]
