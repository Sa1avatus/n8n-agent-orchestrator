# Dub dashboard: choice of the result format

> Note: the reference is the approved mockup `audio-translation/docs/ui-mockup/output-format.html`
> (open it with `#s1` for New job and `#s2` for Queue). The built pages must match its layout, texts and states, not its pixels.
> Language: the interface is checked in Russian; dark is the default theme.

## New job
- Open: route /, tab "Новая задача", viewport 1440x900
- Layout: card "3. Результат" starts with a segmented control "Что получить" with four options, under it the fields "Название дорожки дубляжа", "Громкость оригинала под дубляжом", "Куда сохранить", a grey hint line, then "Модель перевода" and "Параллельных потоков", the buttons "Пробный отрезок 10 мин" and "Добавить в очередь".
- C1: the segmented control has exactly the options "Только звук" (FLAC), "Видео: дубляж + оригинал" (MKV, 2 дорожки), "Видео: только дубляж" (MKV, 1 дорожка), "Видео для телефона" (MP4, 1 дорожка); "Видео: дубляж + оригинал" is selected by default and exactly one option is selected.
- C2: "Куда сохранить" shows the read-only path outputs/<job name>/ and the grey hint says that the video is not re-encoded and the format can be changed later.
- C3: the fields "Название дорожки дубляжа" and "Громкость оригинала под дубляжом" are hidden or disabled for "Только звук".
- C4: the job created from the form carries the chosen format; the API answer of the created job shows it in the field `output_format`.

## Queue
- Open: route /, tab "Очередь", viewport 1440x900
- Layout: under a finished job row a result panel "Результат · outputs/<name>/" lists the files with their kind and size, each with "Скачать"; the video file also has "Открыть папку"; the last line offers "Собрать в другом виде" with a select and the button "Собрать".
- C5: a finished job lists every produced file (video and/or mix.flac) with a size; the files exist on the host under outputs/<job name>/ (the dashboard reports the host path).
- C6: "Собрать в другом виде" builds the chosen format from the stored mix without running any dubbing stage again (the job's stage list shows only the remux stage added).
- C7: a failed or running job shows no result panel.
- C11: a failed or interrupted job row shows the button "Продолжить" with a hint of how many scenes and batches are skipped; a running or finished job does not show it.
- C8: every icon-only button has an accessible name (aria-label).

## Result files (checked by command, not by screenshot)
- C9: for "Видео: дубляж + оригинал" ffprobe shows the original video stream copied (same codec) and two audio streams titled "dub" and the original; for "Видео: только дубляж" one audio stream; for MP4 one audio stream and container mp4; for "Только звук" a flac file only.
- C10: the mix and remux stages of a 60-minute video finish: the stage timeout is derived from the duration, a short video keeps a short bound.
- C12: scenes of a new job end at pauses: no scene of a test job is longer than the max_seconds setting except a scene of one long segment, and the scene durations in the job record are shown in the monitor (checked by command on the job record, not by screenshot).

## Settings
- Open: route /, tab "Настройки", viewport 1440x900
- Layout: a card "Сцены" next to "Видеокарта и потоки" with two number fields and a hint; the buttons "Показать изменения" and "Сохранить" below the cards.
- C13: the card "Сцены" has the fields "Реплик в сцене, максимум" and "Длительность сцены, максимум, с" with a label above each, a hint that a running job keeps its cut, and values loaded from the settings API.
- C14: a value of 0 or a negative number is rejected with a visible message and nothing is saved; a valid change appears in "Показать изменения" before saving.

## Voice only
- Open: route /, tab "Новая задача", viewport 1440x900, subtitles chosen in the dub language
- C15: card "2. Откуда брать текст" shows the note "Субтитры на языке дубляжа: перевод не нужен" and a select "Перевод" with the options "Авто", "Всегда", "Никогда"; the note is absent when the subtitle language differs from the dub language.
- C16: a job created with equal languages and "Авто" reports the translate stage as skipped with its reason and the gateway received no translation request.
