# Установка монтажёра Reels — инструкция для Claude Code

> **Claude, эта инструкция для тебя.** Владелец дал тебе ссылку на репозиторий и попросил развернуть монтажёра.
> Иди по шагам сверху вниз. После каждого шага проверяй результат командой из шага. Где написано
> **СПРОСИ** — задай владельцу вопрос вариантами на выбор и дождись ответа. Ничего не удаляй и не
> публикуй без его «да». Ключи и коды входа владелец вводит сам, в чат их не проси.

Что получится: владелец присылает вертикальное видео «говорящая голова», пишет «смонтируй» — и получает
готовый ролик: паузы и растянутые «ааа/эээ» вырезаны, фирменные анимированные субтитры, лёгкая
кинематографическая цветокоррекция, зум на склейках, по желанию вставки и обложка.

**Требования:** Mac (Apple Silicon или Intel) на macOS 13+, ~3 ГБ свободного места (модели распознавания),
Homebrew, Python 3.9+, Claude Code. Речь по умолчанию русская.

---

## Шаг 0. Где будет жить код
**СПРОСИ:** куда положить проект? Рекомендация — `~/dev/reels-editor` (вне iCloud: папки Рабочий стол/Документы
часто синхронизируются, и тяжёлые файлы начнут гонять туда-сюда).
```bash
mkdir -p ~/dev && cd ~/dev && git clone <ССЫЛКА_НА_РЕПОЗИТОРИЙ> reels-editor && cd reels-editor
```
Дальше `REPO` = полный путь к этой папке.

## Шаг 1. Системные программы
```bash
xcode-select -p || xcode-select --install     # swiftc для поиска лица (Apple Vision)
brew install ffmpeg whisper-cpp
```
Проверка: `ffmpeg -version`, `which whisper-cli`, `which swiftc`.
Если `xcode-select --install` открыл окно — владелец нажимает «Установить» сам и ждёт окончания.

## Шаг 2. Python-библиотеки
```bash
python3 -m pip install --user numpy pillow vosk
```

## Шаг 3. Модель whisper (текст речи, ~1,6 ГБ)
```bash
mkdir -p ~/.cache/whisper-cpp && cd ~/.cache/whisper-cpp
curl -L -C - --retry 20 -o ggml-large-v3-turbo.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin
ls -lh ggml-large-v3-turbo.bin     # ≈ 1,5-1,6 ГБ
```
На медленной сети это долго — предупреди владельца и запусти в фоне. **Одна загрузка в один файл**: два
параллельных curl в один файл портят его.

## Шаг 4. Модель Vosk (точное время слов, ~45 МБ)
```bash
mkdir -p ~/.cache/vosk && cd ~/.cache/vosk
curl -L -C - --retry 20 -o small-ru.zip https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip
unzip -t small-ru.zip > /dev/null && unzip -q small-ru.zip && rm small-ru.zip
ls ~/.cache/vosk     # vosk-model-small-ru-0.22
```
Для другого языка — модель с https://alphacephei.com/vosk/models и поле `vosk_model` в настройках.

## Шаг 5. Настройки
```bash
mkdir -p ~/.config/reels-editor
cp REPO/config.example.json ~/.config/reels-editor/config.json
```
**СПРОСИ** (можно одним списком вопросов с вариантами):
1. Где хранить рабочие папки роликов? (по умолчанию `~/Movies/Монтаж`) → `work_dir`
2. Язык речи? (по умолчанию `ru`) → `language` (+ модель Vosk для этого языка)
3. Цвет акцента субтитров: лайм (по умолчанию), жёлтый или свой HEX → `accent`
4. Цветокоррекция: `cine` (по умолчанию), `soft`, `strong`, `none` → `grade.preset`
5. Нужна ли связка с Telegram (видео в «Избранное» → монтаж → готовый ролик обратно в «Избранное»)? → `telegram`
6. Стиль субтитров оставить C «заливка» или показать три стиля на первом ролике? → `style`

## Шаг 6. Скилл для Claude Code
```bash
mkdir -p ~/.claude/skills/reels-editor
sed "s#{{REPO}}#$(cd REPO && pwd)#g" REPO/skill/SKILL.md > ~/.claude/skills/reels-editor/SKILL.md
```
Проверка: в файле нет строки `{{REPO}}`.

## Шаг 7. Проверка окружения
```bash
cd REPO && python3 -m reels doctor
```
Все строки должны быть ✅. На ❌ — выполнить подсказку справа и повторить.

## Шаг 8 (если выбран Telegram). Вход в свой Telegram
1. Владелец сам открывает https://my.telegram.org → **API development tools** → создаёт приложение
   (название любое) и получает `api_id` и `api_hash`.
2. Владелец сам создаёт файл `~/.config/reels-editor/telegram.env` (ключи в чат не присылать):
   ```
   TG_API_ID=1234567
   TG_API_HASH=0123456789abcdef0123456789abcdef
   ```
3. `python3 -m pip install --user telethon`
4. Владелец сам в Терминале запускает `cd REPO && python3 -m reels tg-login` и вводит телефон и код из Telegram.
   Файл сессии появится в `~/.config/reels-editor/telegram.session` — это ключ к аккаунту, никому не отправлять.
5. Проверка: `python3 -m reels fetch --list` показывает видео из «Избранного» (или пустой список).
Отправка монтажёром идёт **только в «Избранное» самого владельца**, адресат зашит в коде.

## Шаг 9. Тестовый прогон
**СПРОСИ** владельца короткое видео (20-60 с, вертикально, говорит в камеру): путь к файлу, «Загрузки»
(AirDrop) или «Избранное» Telegram.
```bash
cd REPO
python3 -m reels new <путь_к_видео> --name test_1
python3 -m reels render test_1
```
Открой `<work_dir>/test_1/qa_sheet.jpg` и посмотри глазами: субтитры читаются, ниже лица, цвет тёплый.
Если стиль ещё не выбран: `python3 -m reels styles test_1 --sec 8` и покажи три варианта.
Покажи владельцу `final.mp4`. Если всё хорошо — установка закончена, дальше работа по скиллу.

## Если что-то пошло не так
- `whisper-cli упал` — не докачалась модель (шаг 3), сверь размер файла.
- `Vosk недоступен` в строке «выравнивание» — нет модели или библиотеки (шаги 2, 4). Монтаж работает и без неё, но склейки менее точные.
- Субтитры на лице — Apple Vision не нашёл лицо (плохой свет); ставится высота по умолчанию.
- `мало места на диске` — нужно свободного места ≈ 3 × размер исходника.
