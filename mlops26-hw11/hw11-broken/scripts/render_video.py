"""Собрать озвученное MP4 из HTML, SAPI и фактических результатов команд.

Запуск: uv run --no-sync python -m scripts.render_video
Нужны установленные ffmpeg, русский голос SAPI и встроенный runtime Playwright.
Пакеты не устанавливаются. Временные материалы сохраняются в .local/video-clean.
"""

from __future__ import annotations

import concurrent.futures
import json
import shutil
import subprocess
import time
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".local" / "video-clean"
OUTPUT = ROOT / "reports" / "video" / "hw11-voiced.mp4"
NARRATION = [
    "В видео разобрана одиннадцатая домашняя работа по дисциплине Эм эл опс. Цель — разместить сервис в Кубернетес, воспроизвести эксплуатационные проблемы и подтвердить исправления измерениями. Рассмотрены ранняя готовность, обрывы ответов при обновлении и перегрузка одной реплики. Все числа получены на настоящем локальном стенде, но генерацию имитирует управляемая программа на процессоре, а не языковая модель.",
    "Сервис развивает интерфейс из предыдущих домашних работ. Четыре клиентских контейнера имеют разные ай пи адреса. Каждый запрос проходит через один из двух сервисов: обычный либо с привязкой клиента к реплике. За ними работают одни и те же реплики приложения. Прометеус собирает метрики каждого контейнера, а Графана показывает задержки, очередь и ресурсы. Артефакт проверяется по контрольной сумме. История диалога передаётся в запросе, поэтому замена контейнера не уничтожает единственную копию разговора.",
    "На экране — фактический вывод команды проверки кластера, выполненной при подготовке этого видео. Видны контейнеры, развёртывания и оба сервиса. Используется локальный кластер кайнд, Кубернетес версии один тридцать пять и Хелм четвёртой версии. Контейнерному окружению доступны четыре процессорных ядра и примерно семь целых семь десятых гибибайта памяти. Для каждой реплики приложения заданы запросы и пределы ресурсов. В исходной интерактивной презентации эту команду можно повторить кнопкой. В готовом видео показан сохранённый результат реального запуска.",
    "Имитатор позволяет управлять причинами задержек. Прогрев длится двадцать секунд. Одновременно выполняется одна генерация, ещё двенадцать запросов могут ждать. Время ожидания в очереди ограничено пятнадцатью секундами. Локальный префиксный кеш хранит шестнадцать ключей. Попадание стоит двадцать пять тысячных процессорной секунды, промах — две десятых. Ответ состоит из частей. Каждая часть требует вычисления и заданного ожидания. Поэтому здесь измеряется время до первой части ответа. Называть его временем до токена настоящей языковой модели было бы неверно.",
    "Для сравнения балансировки выполнены двенадцать основных прогонов: две политики, два профиля трафика и три повтора. Каждый прогон длится сто двадцать секунд и содержит сто восемьдесят запросов. При перекосе ровно восемьдесят процентов поступают от одного клиента. В сравниваемых вариантах совпадают входы, длины, лимиты ответа и расписание отправки. Перед каждым прогоном реплики заменяются, кеш пуст, прогрев завершён. Долгоживущие соединения отключены. Записываются плановое и фактическое время отправки, обслуживший контейнер и исход каждой попытки.",
    "Первый дефект — преждевременная готовность. В исходном режиме запуск веб сервера считался достаточным: контейнер объявлялся готовым, хотя вычислительная часть ещё прогревалась. В результате сорок запросов из ста сорока были отклонены холодным обработчиком. После исправления завершились все сто сорок запросов. Проверка запуска ждёт прогрева. Проверка готовности дополнительно учитывает прекращение приёма новых запросов. Проверка жизнеспособности контролирует доступность сервера. На графиках показаны готовые контейнеры и точки обслуживания, а крестиками отмечены неуспешные попытки.",
    "Второй дефект проверяется обновлением версии эй на версию би. До начала обновления запускается длинный потоковый ответ. При времени завершения в одну секунду два из девяти потоков оборвались. Код ответа двести уже был получен, но пришли только двадцать четыре и шестьдесят пять частей вместо ста. После исправления завершились все девять ответов. Перед остановкой контейнер прекращает приём новых запросов, ждёт пять секунд распространения состояния и завершает активные потоки. Общий срок штатного завершения увеличен до шестидесяти секунд.",
    "Следующая проверка — выпуск заведомо неготовой версии би и управляемый откат. Новая реплика оставалась неготовой в списке точек обслуживания и не получала рабочие запросы. Все девяносто запросов завершились с версией эй. После отката это подтвердил отдельный ответ сервиса. Затем контейнер заменили полностью. Имя контейнера изменилось, но версия и контрольная сумма артефакта сохранились. Диалог с контрольной строкой альфа продолжился, а в отдельном диалоге бета этой строки не оказалось. Следовательно, состояние не привязано к локальному кешу.",
    "Здесь показан настоящий ответ на короткий запрос через сервис. В нём есть имя обслужившего контейнера, фактически загруженная версия, контрольная сумма и явная отметка режима имитации. Также возвращается переданная история с контрольной строкой видео демо. Эти поля важны: имя модели из конфигурационного файла само по себе не доказывает, что именно эта версия загружена в память. Команда выполнена при сборке видео. В интерактивном варианте её можно повторить и получить новое имя контейнера и новый идентификатор запроса.",
    "Принудительное завершение процесса исследуется отдельно от штатного обновления. Активному процессу отправлен сигнал немедленной остановки. Из тридцати шести попыток четырнадцать завершились, двадцать были отклонены очередью, две прервались. Одна из них уже получила код двести и часть потока. Вторая потеряла соединение до получения заголовков. Все отказы нельзя приписать аварии: длинные ответы здесь намеренно перегружают очередь. После перезапуска проверочный запрос снова получил код двести. Успех потока определяется завершающим событием, а не только успешным открытием соединения.",
    "Теперь сравним политики балансировки. Каждая точка на графике — девяносто пятый перцентиль одного прогона по завершённым ответам. При перекосе привязка по ай пи дала ожидание примерно двенадцать с половиной секунд и десятки отказов. Обычный сервис дал от одной целой трёх десятых до четырёх целых трёх десятых секунды, без отказов во всех трёх повторах. Это подтверждает, что в данном профиле фиксированное назначение горячего клиента создаёт устойчивую перегрузку одной реплики.",
    "На этом графике видна причина. В третьем повторе сто шестьдесят восемь попыток пришлись на один контейнер и только двенадцать — на соседний. У горячей реплики очередь достигает предельных двенадцати запросов. У другой очередь остаётся пустой. Время до первой части у горячей реплики растёт вместе с ожиданием. Привязка по ай пи сохраняет прежнее назначение и не учитывает свободные слоты соседа. Поэтому выбранное исправление для этого стенда — отключить привязку при перекошенной нагрузке.",
    "Однако вывод о безусловном вреде привязки был бы неверным. На равномерном трафике она во всех повторах распределила запросы поровну: девяносто на девяносто. Задержки оказались меньше, чем у обычного сервиса. Случайное распределение новых соединений у обычного сервиса создаёт кратковременные очереди и хуже сохраняет локальность кеша. Поэтому выбор защищается по конкретному профилю нагрузки. Для настоящего движка генерации отдельно нужна проверка маршрутизации, которая одновременно учитывает стоимость пересчёта префикса и длину очереди.",
    "Корректность измерений проверена отдельно. Клиент использует монотонные часы и различает время до первой части, полную длительность и интервалы между частями. Для сравнения с Прометеус берётся та же популяция успешно завершённых ответов. Обнаружена проблема слишком широкого интервала гистограммы: от двенадцати до двадцати секунд. Он завышал оценку девяносто пятого перцентиля. После уточнения границ отдельный прогон дал двенадцать целых триста девяносто шесть тысячных секунды против клиентских двенадцати целых триста двадцать две тысячных. Расхождение — шесть десятых процента, число наблюдений в обоих источниках равно ста пятидесяти.",
    "Для автоматического масштабирования настроен диапазон от двух до трёх реплик. Целевая загрузка процессора — шестьдесят процентов от запрошенного ресурса. Нагрузка в этом опыте специально сделана вычислительной: пять запросов в секунду в течение трёх минут. На графике виден переход от двух реплик к трём и возврат к двум после снижения нагрузки. Новый контейнер появился примерно через двадцать три секунды от начала отправок и стал готовым примерно через пятьдесят пять. Это отдельный профиль; его задержки не сравниваются с предыдущими опытами как эффект одного только масштабирования.",
    "Повтор с привязкой показывает ограничение масштабирования. При обычном сервисе добавленная реплика получила сто девяносто пять запросов из девятисот. При привязке она не получила ни одного. Распределение составило восемьсот сорок, шестьдесят и ноль. Контроллер добавил вычислительную ёмкость, но назначения существующих клиентов не изменились. Средняя загрузка может превысить порог из-за одного горячего контейнера, даже когда сосед простаивает. Возврат к двум репликам произошёл примерно через сто пять и девяносто три секунды после последней отправки.",
    "Настроены два предупреждения. Первое реагирует на рост девяносто пятого перцентиля времени до первой части выше полутора секунд. Второе — на долю отказов выше десяти процентов. Оба требуют минимального числа наблюдений за тридцать секунд и удержания условия в течение пятнадцати секунд. В журналах подтверждены ожидание срабатывания, активное предупреждение и возврат в норму. Собственные проверки покрывают прогрев, откат, восстановление версии и истории, корректный учёт обрывов и изоляцию двух диалогов.",
    "Перед сборкой видео повторно выполнены быстрые проверки. Сначала проверяются стиль кода, форматирование и девять локальных тестов. Затем отдельная проверка сверяет сырые доказательства: все двенадцать сравнительных прогонов, готовность, обновление, откат, восстановление, аварийное завершение, оба цикла масштабирования и предупреждения. На экране — фактический вывод этих команд. Долгие нагрузочные опыты при этом не запускаются заново. Поэтому быстрый результат не заменяет эксперименты: он проверяет уже сохранённые данные и их полноту.",
    "На ревью предложена постоянная привязка диалога к контейнеру. При заданных условиях новый запрос лучше отправить свободной реплике би: пересчёт занимает восемь десятых секунды, тогда как очередь эй уже добавляет три секунды. Нужны одинаковая версия и полная история, а также свежие данные для допуска запроса. Уже начатый поток остаётся у эй до завершения. С этим согласуется кейс Гугл из лекции: команда увеличила вес очереди в маршрутизации и применила контроль допуска. Сообщается снижение девяносто пятого перцентиля времени до токена на пятьдесят два процента. Это результат Гугл, а не нашего имитатора.",
    "Итог работы: после исправления готовности устранены сорок отказов из ста сорока, после исправления обновления — два обрыва из девяти. Обычный сервис не дал отказов в трёх прогонах с перекосом. Проверены восстановление диалога, предупреждения и два цикла масштабирования. При этом один локальный узел и имитатор на процессоре не доказывают межмашинную отказоустойчивость, качество языковой модели или поведение видеокарты и кеша настоящего движка. Код, исходные данные, отчёт и графики сохранены в репозитории.",
]

TTS = r"""
param([string]$Manifest)
$ErrorActionPreference = 'Stop'
$items = [System.IO.File]::ReadAllText($Manifest, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$voice = New-Object -ComObject SAPI.SpVoice
$selected = @($voice.GetVoices() | Where-Object { $_.GetDescription() -match 'Irina' })
if ($selected.Count -eq 0) { throw 'Microsoft Irina voice is not installed' }
$voice.Voice = $selected[0]
$voice.Rate = 0
$voice.Volume = 100
foreach ($item in $items) {
    $stream = New-Object -ComObject SAPI.SpFileStream
    $stream.Format.Type = 22
    $stream.Open($item.wav, 3, $false)
    try {
        $voice.AudioOutputStream = $stream
        [void]$voice.Speak($item.text, 0)
    } finally { $stream.Close() }
    Write-Output ("Synthesized slide " + $item.slide)
}
"""

BROWSER = r"""
const fs = require('node:fs'), path=require('node:path');
const {chromium}=require(process.argv[2]);
(async()=>{
const output=process.argv[3];
const browser=await chromium.launch({executablePath:process.argv[4],headless:true});
const page=await browser.newPage({viewport:{width:1920,height:1080}});
await page.goto('http://127.0.0.1:8761',{waitUntil:'networkidle'});
await page.addStyleTag({content:'#footer button,#clock{display:none!important} #caption{font-size:18px} #counter{margin-right:30px}'});
await page.evaluate(()=>{auto=false;updateTimer();document.querySelector('.cover .eyebrow').textContent='MLOps · домашняя работа 11'});
const manifest=[];
for(let i=0;i<20;i++){
 await page.evaluate(i=>{show(i);document.getElementById('caption').textContent='ДЗ 11 · Kubernetes под нагрузкой · CPU backend: simulated'},i);
 const frames=[];
 const shot=async suffix=>{const p=path.join(output,'slide-'+String(i+1).padStart(2,'0')+'-'+suffix+'.png');await page.screenshot({path:p});frames.push(p)};
 const ids=i===2?['cluster']:i===8?['api']:i===17?['tests','evidence']:[];
 if(!ids.length){await shot('screen')}
 for(const id of ids){
   await page.locator('[data-command="'+id+'"]').click();
   await page.waitForFunction(()=>{const e=document.querySelector('.slide.active .status');return e.classList.contains('success')||e.classList.contains('error')},null,{timeout:130000});
   const state=await page.locator('.slide.active .status').innerText();
   if(!state.startsWith('Успешно')) throw Error(id+': '+await page.locator('.slide.active .terminal').innerText());
   await page.evaluate(()=>{document.querySelector('.slide.active .status').textContent+=' · Выполнено при сборке видео';document.querySelector('.slide.active .terminal').scrollTop=0});
   await shot(id+'-top');
   const scroll=await page.evaluate(()=>{const e=document.querySelector('.slide.active .terminal');if(e.scrollHeight>e.clientHeight+3){e.scrollTop=e.scrollHeight;return true}return false});
   if(scroll)await shot(id+'-bottom');
 }
 manifest.push({slide:i+1,frames});
 console.log('Rendered slide '+(i+1));
}
fs.writeFileSync(path.join(output,'frames.json'),JSON.stringify(manifest,null,2));
await browser.close();
})().catch(e=>{console.error(e);process.exitCode=1});
"""


def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, check=True, **kwargs)


def duration(path: Path) -> float:
    if path.suffix == ".wav":
        with wave.open(str(path), "rb") as stream:
            return stream.getnframes() / stream.getframerate()
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return float(result.stdout)


def encode(item: dict) -> Path:
    index = item["slide"]
    wav = BUILD / f"voice-{index:02}.wav"
    frames = [Path(value) for value in item["frames"]]
    seconds = duration(wav) + 1.2
    concat = BUILD / f"frames-{index:02}.txt"
    lines = []
    for frame in frames:
        lines.extend(
            [f"file '{frame.as_posix()}'", f"duration {seconds / len(frames):.6f}"]
        )
    lines.append(f"file '{frames[-1].as_posix()}'")
    concat.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    segment = BUILD / f"segment-{index:02}.mp4"
    log = BUILD / f"encode-{index:02}.log"
    with log.open("w", encoding="utf-8") as stream:
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat),
                "-i",
                str(wav),
                "-t",
                f"{seconds:.6f}",
                "-vf",
                "fps=25,format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "stillimage",
                "-crf",
                "20",
                "-threads",
                "2",
                "-af",
                "apad",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "48000",
                "-movflags",
                "+faststart",
                str(segment),
            ],
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    print(f"Encoded slide {index}: {seconds:.1f} seconds", flush=True)
    return segment


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg/ffprobe are required; nothing was installed.")
    runtime = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
    )
    playwright = runtime / "node_modules" / "playwright"
    node = runtime / "bin" / "node.exe"
    chrome = (
        Path("C:/")
        / "Program Files"
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe"
    )
    if not all(path.exists() for path in (playwright, node, chrome)):
        raise SystemExit("Bundled Playwright/Node or existing Chrome is missing.")
    response = httpx.get("http://127.0.0.1:8761/presentation-health", timeout=5)
    response.raise_for_status()
    if response.json().get("app") != "mlops-hw11-presentation":
        raise SystemExit("Start scripts.presentation_server first.")
    manifest = [
        {"slide": i + 1, "text": text, "wav": str(BUILD / f"voice-{i + 1:02}.wav")}
        for i, text in enumerate(NARRATION)
    ]
    previous = ROOT / ".local" / "video" / "narration.json"
    cached = (
        json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else []
    )
    pending = []
    for item in manifest:
        old = next(
            (
                row
                for row in cached
                if row["slide"] == item["slide"] and row["text"] == item["text"]
            ),
            None,
        )
        if old and Path(old["wav"]).exists():
            shutil.copyfile(Path(old["wav"]), Path(item["wav"]))
        else:
            pending.append(item)
    (BUILD / "pending-narration.json").write_text(
        json.dumps(pending, ensure_ascii=False), encoding="utf-8"
    )
    (BUILD / "narration.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (BUILD / "synthesize.ps1").write_text(TTS, encoding="utf-8", newline="\n")
    (BUILD / "render.cjs").write_text(BROWSER, encoding="utf-8", newline="\n")
    started = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        speech = pool.submit(
            run,
            [
                "powershell.exe",
                "-NoProfile",
                "-File",
                str(BUILD / "synthesize.ps1"),
                "-Manifest",
                str(BUILD / "pending-narration.json"),
            ],
        )
        pictures = pool.submit(
            run,
            [
                str(node),
                str(BUILD / "render.cjs"),
                str(playwright),
                str(BUILD),
                str(chrome),
            ],
        )
        speech.result()
        pictures.result()
    frames = json.loads((BUILD / "frames.json").read_text())
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        segments = list(pool.map(encode, frames))
    (BUILD / "segments.txt").write_text(
        "\n".join(f"file '{p.as_posix()}'" for p in segments),
        encoding="utf-8",
        newline="\n",
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(BUILD / "segments.txt"),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-metadata",
            "title=MLOps HW11",
            "-metadata",
            "comment=Russian narration; CPU simulated backend.",
            str(OUTPUT),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    proof = {
        "duration_seconds": duration(OUTPUT),
        "bytes": OUTPUT.stat().st_size,
        "slides": len(frames),
        "voice": "Microsoft Irina Desktop - Russian",
        "script_author": "ChatGPT",
        "build_seconds": time.time() - started,
        "audio_durations": [duration(Path(item["wav"])) for item in manifest],
    }
    (BUILD / "validation.json").write_text(
        json.dumps(proof, indent=2), encoding="utf-8"
    )
    print(json.dumps(proof, indent=2), flush=True)
    print(f"Video: {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
