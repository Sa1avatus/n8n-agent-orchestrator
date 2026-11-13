# Dub dashboard: redesigned pages

> Note: the reference images are screenshots of the approved mockup
> (`audio-translation/docs/ui-mockup/mockup.html`, rendered with Playwright), not of the built pages.
> The mockup is the agreed look; the built pages must match its layout, texts and states, not its pixels.
> Language: the interface is checked in Russian (the language switch set to RU); dark is the default theme.

## New job
- Open: route /, tab "Новая задача", viewport 1440x900
- Layout: a top bar with the product name, four tabs (Новая задача, Очередь, Монитор, Настройки), a GPU status pill, a theme button and a language button; under it three numbered cards: 1 source (a dashed drop zone with "Выбрать файлы" and "Папка на сервере", the detected file line with a pill "найдены субтитры: eng, rus"), 2 text source (a segmented control with "Распознать звук", "Субтитры", "Субтитры + звук", then three selects and a checkbox), 3 result (three selects, an estimate line, a secondary and a primary button)
- Reference image: new-job-desktop-dark.png
- C1: the top bar shows the product name "Дубляж", four tabs "Новая задача", "Очередь", "Монитор", "Настройки" with the current one highlighted, a status pill, a theme button and a language button.
- C2: card "1. Источник" shows a drop zone with the text "Перетащите файлы или папку сюда" and two buttons "Выбрать файлы" and "Папка на сервере".
- C3: card "2. Откуда брать текст" shows a segmented control with exactly the options "Распознать звук", "Субтитры", "Субтитры + звук" and one of them is marked as selected; the control and its selects appear only when the API reports the text-source fields.
- C4: the checkbox "Озвучивать короткие реплики и выкрики толпы" is in card 2.
- C5: card "3. Результат" shows the selects "Что получить", "Модель перевода", "Параллельных потоков" (the last only when the API reports it), the buttons "Пробный отрезок 10 мин" and "Добавить в очередь"; the primary button is the filled one.
- C6: every select has its label above it and no label is glued to a value (a label and its value are separate lines).

## Queue
- Open: route /, tab "Очередь", viewport 1440x900
- Layout: one card "Очередь" with a header row (Задача, Статус, Этап и прогресс, Осталось) and one row per job: name with a grey second line (language pair and text source), a status pill, the stage and a progress bar, the time left, and an icon button (pause, remove, download or retry)
- Reference image: queue-desktop-dark.png
- C7: each job row has a status pill whose colour AND text differ per state: "идёт", "ждёт", "готово", "ошибка".
- C8: a running job shows its stage name with the position (for example "Синтез · 214 из 342") and a progress bar.
- C9: a finished job shows a summary text instead of a progress bar and a download button; a failed job shows the reason and a retry button.
- C10: every icon-only button has an accessible name (aria-label).

## Monitor
- Open: route /, tab "Монитор", viewport 1440x900
- Layout: a row of metric tiles (label above the value, the unit smaller): Видеопамять, Загрузка GPU, Скорость, Диск задач; a card "Кто занимает видеокарту" with one row per holder; a card with per-stage time bars for an hour of audio
- Reference image: monitor-desktop-dark.png
- C11: the tiles "Видеопамять", "Загрузка GPU", "Скорость" and "Диск задач" show the label on its own line above the value, with a space and the unit after the number (never "GPU utilisation3 %").
- C12: the card "Кто занимает видеокарту" lists a stage, the memory used and the stream per row, or an empty state text when nothing holds the GPU.
- C13: the per-stage time card shows one horizontal bar per stage with the stage name on the left and the time on the right.

## Settings
- Open: route /, tab "Настройки", viewport 1440x900
- Layout: grouped cards: "Перевод" (gateway URL, type, key STATUS with a button, a test button and a result line), "Видеокарта и потоки" (streams, VRAM budget, speech speed-up limit); a bottom row with "Показать изменения" and the primary "Сохранить"
- Reference image: settings-desktop-dark.png
- C14: card "Перевод" shows the fields "Адрес шлюза", "Тип", a key status pill ("не задан" or "задан") and the button "Проверить перевод"; the key value is never shown anywhere.
- C15: card "Видеокарта и потоки" shows "Потоков одновременно", "Бюджет видеопамяти, ГБ" and "Потолок ускорения речи".
- C16: the buttons "Показать изменения" and "Сохранить" are at the bottom right, "Сохранить" is the filled one.

## New job on a phone
- Open: route /, tab "Новая задача", viewport 390x844
- Layout: the same cards stacked in one column; the tabs and chips wrap to several lines; nothing is wider than the screen
- Reference image: new-job-mobile-dark.png
- C17: the page has no horizontal scroll (document width equals the viewport width) and no control is cut off at the right edge.
- C18: the four tabs, the segmented control and both buttons of card 3 are reachable without zooming.

## Queue on a phone
- Open: route /, tab "Очередь", viewport 390x844
- Layout: each job is a two-column block instead of a five-column row
- Reference image: queue-mobile-dark.png
- C19: a job block shows its name, status pill and stage without overlapping text, and the page has no horizontal scroll.

## New job in the light theme
- Open: route /, tab "Новая задача", theme light (the theme button once), viewport 1440x900
- Layout: the same page with the light tokens
- Reference image: new-job-desktop-light.png
- C20: every text is readable against its background (contrast AA), the cards are separated from the page background by a border, and the selected segment and the primary button are clearly marked.
