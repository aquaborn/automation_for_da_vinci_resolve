# automation_for_da_vinci_resolve

Скрипты для автоматизации DaVinci Resolve Studio.

## Черновой монтаж по тишине

Первый MVP лежит в `scripts/rough_cut_silence.py`.

Что делает:

- анализирует экспортированное аудио таймлайна через `ffmpeg`;
- находит длинные паузы;
- предлагает безопасные участки для сокращения;
- пишет отчеты в `json` и `csv`;
- опционально пробует поставить маркеры на текущий таймлайн DaVinci Resolve.

Сначала экспортируй звук из таймлайна в WAV/AIFF/MP3, потом запусти:

```bash
python3 scripts/rough_cut_silence.py /path/to/timeline_audio.wav
```

Нужен установленный `ffmpeg`.

Более быстрый YouTube-пресет:

```bash
python3 scripts/rough_cut_silence.py /path/to/timeline_audio.wav \
  --config examples/rough_cut_fast_youtube.json
```

Те же настройки вручную:

```bash
python3 scripts/rough_cut_silence.py /path/to/timeline_audio.wav \
  --noise-db=-38dB \
  --min-silence 0.8 \
  --keep-before 0.12 \
  --keep-after 0.18 \
  --max-cut 8.0
```

Добавить маркеры в открытый таймлайн Resolve:

```bash
python3 scripts/rough_cut_silence.py /path/to/timeline_audio.wav \
  --add-resolve-markers \
  --timeline-fps 25
```

Скрипт не режет исходный таймлайн. Он только подсказывает места, которые можно
сократить, чтобы жена могла быстро пройтись по маркерам и принять решения руками.
