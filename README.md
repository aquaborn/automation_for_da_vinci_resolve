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

Главная настройка ритма:

```bash
--cut-every-beats 8
```

В пресете по умолчанию включен спокойный режим: на таймлайн попадают только
`CUT_POINT` и `DROP`, без каждого обычного бита. Если маркеров все еще много,
поставь `12` или `16`. Если нужно больше точек для очень быстрого монтажа,
поставь `4`.

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
| `cut_every_beats` | Как часто предлагать точку реза. | `4` - чаще, `8` - спокойно, `16` - редко. |
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

Поставить маркеры тишины внутри текущего `In/Out`:

```bash
python3 scripts/resolve_inout_tools.py silence \
  --audio /path/to/inout_audio.wav \
  --config examples/rough_cut_fast_youtube.json \
  --timeline-fps 25
```

Поставить beat/cut-маркеры внутри текущего `In/Out`:

```bash
python3 scripts/resolve_inout_tools.py beat \
  --audio /path/to/inout_music.wav \
  --config examples/beat_cut_shorts.json \
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
- `Workspace > Scripts > Edit > AVDR Silence InOut`

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
