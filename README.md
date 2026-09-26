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
сократить, чтобы монтажер мог быстро пройтись по маркерам и принять решение
руками.

Параметры пресета `examples/rough_cut_fast_youtube.json`:

| Параметр | Что делает | Как настраивать |
| --- | --- | --- |
| `noise_db` | Порог тишины для `ffmpeg`. | `-32dB` агрессивнее, `-45dB` осторожнее. |
| `min_silence` | Минимальная длина паузы, которую скрипт считает кандидатом на сокращение. | Больше значение - меньше найденных пауз. |
| `keep_before` | Сколько секунд оставить перед возвращением речи. | Увеличь, если рез слишком близко подходит к началу слова. |
| `keep_after` | Сколько секунд оставить после окончания речи. | Увеличь, если скрипт съедает дыхание или естественный хвост фразы. |
| `max_cut` | Максимальная длина предлагаемого выреза. | Уменьши, если длинные паузы лучше проверять руками. |

## Паузы речи через Whisper

Скрипт `scripts/speech_pause_markers.py` ищет не просто тишину, а паузы между
словами по Whisper word timestamps.

Что умеет:

- находит паузы между словами;
- помечает протяжные `эээ`, `ааа`, `ммм`, `ууу`, `иии`;
- помечает длинные слова-паразиты вроде `ну`, `типа`, `короче`;
- пишет отчеты в `json` и `csv`;
- через Resolve-команду ставит маркеры внутри текущего `In/Out`.

Для работы нужен один Whisper-бэкенд. Рекомендуемый вариант:

```bash
python3 -m pip install faster-whisper
```

Запуск вручную:

```bash
python3 scripts/speech_pause_markers.py /path/to/voice.wav \
  --config examples/speech_pauses_russian.json
```

Цвета маркеров:

- желтый `SPEECH PAUSE` - пауза между словами;
- оранжевый `FILLER` - слово-паразит или протяжный звук;
- красный `LONG FILLER` - особенно длинный протяжный звук.

Главные настройки `examples/speech_pauses_russian.json`:

| Параметр | Что делает | Как настраивать |
| --- | --- | --- |
| `model` | Модель Whisper. | `small` - хороший старт, `base` быстрее, `medium` точнее. |
| `language` | Язык речи. | Для русского `ru`. |
| `min_pause` | Минимальная пауза между словами. | Уменьши, если нужно ловить короткие паузы. |
| `max_pause` | Максимальная пауза-кандидат. | Больше значения могут быть сценными разрывами. |
| `keep_before` | Сколько оставить перед следующим словом. | Защищает начало следующего слова от слишком близкого реза. |
| `keep_after` | Сколько оставить после предыдущего слова. | Защищает хвост предыдущей фразы. |
| `long_filler` | Минимальная длина слова-паразита. | Уменьши, если нужно ловить короткие `эээ`. |
| `filler_words` | Список слов-паразитов. | Можно дополнять под стиль речи. |

## Монтажный ритм по музыке

Второй MVP лежит в `scripts/beat_cut_markers.py`.

Что делает:

- анализирует музыкальный трек или общий аудио-микс через `ffmpeg`;
- оценивает BPM;
- строит сетку битов;
- отмечает обычные биты, сильные доли, предполагаемые точки реза и пики энергии;
- пишет отчеты в `json` и `csv`;
- опционально ставит маркеры в текущий таймлайн DaVinci Resolve.

Запуск для коротких вертикальных форматов:

```bash
python3 scripts/beat_cut_markers.py /path/to/music.wav \
  --config examples/beat_cut_shorts.json
```

Добавить маркеры в открытый таймлайн Resolve:

```bash
python3 scripts/beat_cut_markers.py /path/to/music.wav \
  --config examples/beat_cut_shorts.json \
  --add-resolve-markers \
  --timeline-fps 25
```

Главный режим ритма:

```json
"marker_strategy": "onset_peaks"
```

В этом режиме скрипт ставит маркеры на реальные пики атаки в аудио, то есть на
те самые вертикальные удары, которые видно на waveform. Это лучше для монтажа,
чем абстрактная BPM-сетка.

Для WAV есть запасной декодер на стандартной библиотеке Python. Для MP3, AIFF,
M4A и других форматов нужен установленный `ffmpeg`.

Параметры пресета `examples/beat_cut_shorts.json`:

| Параметр | Что делает | Как настраивать |
| --- | --- | --- |
| `sample_rate` | Частота дискретизации для анализа. | `22050` обычно достаточно для поиска ритма. |
| `hop_size` | Шаг анализа в сэмплах. | Меньше значение - точнее тайминг, но чуть тяжелее анализ. |
| `bpm_min` | Нижняя граница поиска BPM. | Уменьши для очень медленной музыки. |
| `bpm_max` | Верхняя граница поиска BPM. | Увеличь для очень быстрой музыки. |
| `beats_per_bar` | Количество битов в такте. | Для большинства поп-музыки и электроники подходит `4`. |
| `marker_strategy` | Способ поиска маркеров. | `onset_peaks` - по реальным ударам waveform, `beat_grid` - по BPM-сетке. |
| `onset_peak_percentile` | Насколько сильный пик считать битом. | Ниже значение - больше маркеров, выше - только явные удары. |
| `drop_peak_percentile` | Порог для редких красных `DROP?`. | Увеличь, если красных слишком много. |
| `drop_peak_multiplier` | Насколько `DROP?` должен быть сильнее обычного удара. | Увеличь, если обычные биты становятся красными. |
| `cut_every_beats` | Частота точек реза для режима `beat_grid`. | В `onset_peaks` почти не важен. |
| `max_markers` | Максимум маркеров в отчете и Resolve. | Уменьши, если длинный трек перегружает таймлайн. |
| `include_marker_types` | Какие типы маркеров реально добавлять. | По умолчанию только `CUT_POINT` и `DROP`, чтобы не засорять таймлайн. |
| `min_marker_spacing_seconds` | Минимальная дистанция между маркерами. | Увеличь, если маркеры все еще идут слишком плотно. |
| `marker_duration_frames` | Длительность маркера в Resolve в кадрах. | Обычно достаточно `1`. |
| `marker_color` | Цвет обычных битов. | Используется для `BEAT`. |
| `strong_marker_color` | Цвет сильных долей. | Используется для `STRONG_BEAT`. |
| `cut_marker_color` | Цвет рекомендуемых точек реза. | Используется для `CUT_POINT`. |
| `drop_marker_color` | Цвет пиков энергии. | Используется для `DROP?`. |

## Запуск по In/Out в Resolve

Скрипт `scripts/resolve_inout_tools.py` нужен, чтобы запускать анализ не по
всему таймлайну, а только по выделенному диапазону `In/Out` в текущем таймлайне
Resolve.

Надежный сценарий:

1. В Resolve открой нужный таймлайн.
2. Поставь `In` и `Out` вокруг блока, который нужно обработать.
3. Экспортируй аудио ровно этого диапазона в WAV.
4. Запусти один из режимов ниже.

Поставить beat/cut-маркеры внутри текущего `In/Out`:

```bash
python3 scripts/resolve_inout_tools.py beat \
  --audio /path/to/inout_music.wav \
  --config examples/beat_cut_shorts.json \
  --timeline-fps 25
```

Поставить Whisper-маркеры пауз речи внутри текущего `In/Out`:

```bash
python3 scripts/resolve_inout_tools.py speech \
  --audio /path/to/inout_voice.wav \
  --config examples/speech_pauses_russian.json \
  --timeline-fps 25
```

Если есть аудио всего таймлайна, а не только выбранного блока, можно попросить
скрипт вырезать из него текущий `In/Out` через `ffmpeg`:

```bash
python3 scripts/resolve_inout_tools.py beat \
  --audio /path/to/full_timeline_audio.wav \
  --audio-is-full-timeline \
  --config examples/beat_cut_shorts.json \
  --timeline-fps 25
```

Есть экспериментальный режим, который пытается сам отрендерить аудио текущего
`In/Out` из Resolve:

```bash
python3 scripts/resolve_inout_tools.py beat \
  --render-audio \
  --config examples/beat_cut_shorts.json \
  --timeline-fps 25
```

Этот режим зависит от того, как Resolve принимает render settings через API.
Если он не сработает, используй надежный сценарий с ручным экспортом WAV.

## Команды в меню Resolve

В Resolve нельзя просто добавить свою кнопку на стандартный toolbar через Python
API. Самый близкий и нормальный вариант - добавить команды в меню
`Workspace > Scripts`, а потом при желании назначить им горячие клавиши.

Установить команды меню:

```bash
python3 scripts/install_resolve_menu_scripts.py
```

После установки перезапусти DaVinci Resolve. В меню должны появиться:

- `Workspace > Scripts > Edit > AVDR Beat InOut`
- `Workspace > Scripts > Edit > AVDR Speech Pauses InOut`

Горячие клавиши можно назначить через `DaVinci Resolve > Keyboard Customization`,
найдя эти команды по названию.

Как пользоваться:

1. Открой таймлайн.
2. Поставь `In` и `Out` вокруг нужного блока.
3. Запусти одну из команд из `Workspace > Scripts > Edit`.

Эти команды используют экспериментальный `--render-audio`: они пытаются сами
отрендерить аудио выбранного `In/Out`, проанализировать его и поставить маркеры.
Если на конкретной версии Resolve рендер через API не сработает, используй
ручной сценарий из раздела выше с `--audio /path/to/inout_audio.wav`.
