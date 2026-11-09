# 27B и Flash-Next: все кванты в одной таблице

Оценка: среднее последней оценки рецензента по закрытым задачам. Повторы: попытки сверх первой. Часы Worker, tok/s и цена из статистики раннера (цена: расходы на рецензента, Worker локальный).

|Модель и квант|Тип|Миссия|Статус|Задач|Оценка|Повторы|Часы Worker|tok/s|Цена $|
|---|---|---|---|---:|---:|---:|---:|---:|---:|
|27B iq2_s (llama.cpp)|api|bench-rag-api-local|completed|7/7|88.9|4|2.27|–|2.34|
|27B iq2_s (llama.cpp)|web|bench-rag-web-local|failed|2/9|87.0|2|0.22|–|0.88|
|27B iq2_s, повтор|api|bench-rag-api-local-r2|completed|12/12|89.9|2|0.63|–|3.5|
|27B iq2_s, повтор|web|bench-rag-web-local-r2|stopped|0/9|–|0|0.0|–|0|
|27B iq2_s, мышление до 4K|api|bench-rag-api-local-t4k|completed|7/7|91.4|1|2.38|–|1.81|
|27B iq2_s, мышление до 4K|web|bench-rag-web-local-t4k|completed|9/9|89.8|3|2.08|–|2.27|
|27B iq2_s, GPU 96K, до 4K|api|bench-rag-api-local-gpu96-t4k|completed|7/7|90.3|2|2.27|–|0.77|
|27B iq2_s, GPU 96K, до 4K|web|bench-rag-web-local-gpu96-t4k|failed|9/9|86.1|4|3.01|–|1.08|
|27B iq3_xxs|api|bench-rag-api-local-iq3|failed|0/7|–|0|0.27|–|0.0|
|27B iq3_xxs|web|bench-rag-web-local-iq3|failed|0/9|–|0|0.08|–|0.0|
|27B iq3_xxs, повтор|api|bench-rag-api-local-iq3-r2|completed|7/7|88.3|5|2.19|–|2.34|
|27B iq3_xxs, повтор|web|bench-rag-web-local-iq3-r2|completed|9/9|91.1|6|0.89|–|2.68|
|27B iq3_xxs, мышление до 4K|api|bench-rag-api-local-iq3-t4k|completed|7/7|91.1|2|3.4|–|1.68|
|27B iq3_xxs, мышление до 4K|web|bench-rag-web-local-iq3-t4k|completed|9/9|90.9|2|2.33|–|2.07|
|Flash-Next IQ1_M|api|bench-rag-api-strata-iq1m|failed|1/7|15.0|2|0.53|17.7|0.66|
|Flash-Next IQ1_M|web|bench-rag-web-strata-iq1m|failed|9/9|84.0|3|1.13|32.8|2.29|
|Flash-Next IQ1_M, повтор|api|bench-rag-api-strata-iq1m-r2|failed|7/7|84.6|6|2.01|35.1|2.57|
|Flash-Next IQ1_M, повтор|web|bench-rag-web-strata-iq1m-r2|active|0/9|–|0|0.0|–|0|
|Flash-Next IQ2_XS|api|bench-rag-api-strata-iq2xs|completed|7/7|90.1|1|2.95|39.3|1.22|
|Flash-Next IQ2_XS|web|bench-rag-web-strata-iq2xs|active|0/9|–|0|0.0|–|0|
|Flash-Next Q2_0 (первый)|api|bench-rag-api-strata-q20|completed|7/7|89.7|0|1.31|56.8|0.54|
|Flash-Next Q2_0 (первый)|web|bench-rag-web-strata-q20|completed|9/9|89.2|1|0.71|56.8|0.82|
|Flash-Next Q2_0 рабочая (final-t5)|api|bench-rag-api-strata-q20-final-t5|completed|7/7|90.7|1|0.75|64.6|0.71|
|Flash-Next Q2_0 рабочая (final-t5)|web|bench-rag-web-strata-q20-final-t5|completed|9/9|90.8|3|0.51|64.9|1.07|
|Flash-Next Q2_0 рабочая (final-t5)|wsl|bench-wsl-strata-q20-final-t5|completed|7/7|89.6|0|0.6|65.1|0.95|
|Flash-Next Q2_0 tuned|api|bench-rag-api-strata-q20-tuned-q20|completed|7/7|90.6|1|0.85|62.1|4.05|
|Flash-Next Q2_0 tuned|web|bench-rag-web-strata-q20-tuned-q20|completed|9/9|89.6|1|0.61|59.5|3.3|
|Flash-Next Q2_0 tuned|wsl|bench-wsl-strata-q20-tuned-q20|completed|7/7|89.1|2|0.83|61.4|4.49|
|Flash-Next IQ3_XXS tuned|api|bench-rag-api-strata-q20-tuned-iq3|completed|7/7|91.1|1|1.18|51.7|4.23|
|Flash-Next IQ3_XXS tuned|web|bench-rag-web-strata-q20-tuned-iq3|completed|9/9|90.6|3|1.32|52.9|5.32|
|Flash-Next IQ3_XXS tuned|wsl|bench-wsl-strata-q20-tuned-iq3|completed|7/7|89.4|0|1.48|53.7|5.64|

## Как читать

- Оценка рецензента почти не зависит от модели и кванта: у всех завершённых миссий 88–91. Различия в пределах шума, на одном бенче и без повторов выводов по качеству делать нельзя.
- Скорость и время различаются сильно. Flash-Next Q2_0 (рабочая) даёт 64–65 tok/s и 0,5–0,75 ч Worker на миссию. IQ3_XXS на тех же настройках даёт 52–54 tok/s и 1,2–1,5 ч. У 27B с мышлением до 4K уходит 2–3,4 ч.
- Для 27B tok/s в статистике раннера не считается (локальный llama.cpp), поэтому в колонке прочерк.
- IQ1_M: первая попытка api упала (1/7, оценка 15), повтор закончился `failed` при 7/7 закрытых задач (84,6): задач закрыто много, но миссия не завершилась.
- Незавершённые строки (`active`, `stopped`, `failed` с нулём задач) остались от прерванных или остановленных запусков; на выводы не влияют.
- Цена в колонке «Цена $» это расходы на рецензента (Sonnet); у `tuned` она выше, потому что рецензент работал дольше.
- Публичные сравнения квантов: `strata-quants-comparison-ru.md` и ответ выше (Artificial Analysis, Quesma, Unsloth).
