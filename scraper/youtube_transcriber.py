"""Скачивание аудио с YouTube и транскрибация через faster-whisper."""

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import YOUTUBE_AUDIO_DIR, YOUTUBE_TRANSCRIPTS_DIR


def _extract_video_id(url: str) -> str:
    """Извлечь video_id из YouTube URL."""
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    raise ValueError(f"Не удалось извлечь video_id из URL: {url}")


def download_audio(url: str) -> Dict:
    """Скачать аудио с YouTube через yt-dlp.

    Returns:
        dict с ключами: video_id, title, channel, duration, audio_path, url
    """
    import yt_dlp

    video_id = _extract_video_id(url)
    YOUTUBE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    # Проверяем кэш (исключаем .part — незавершённые загрузки)
    existing = [
        f for f in YOUTUBE_AUDIO_DIR.glob(f"{video_id}.*")
        if not f.name.endswith(".part")
    ]
    if existing:
        # Получаем метаданные без скачивания
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        print(f"[YT] Аудио уже скачано: {existing[0].name}")
        return {
            "video_id": video_id,
            "title": info.get("title", ""),
            "channel": info.get("channel", info.get("uploader", "")),
            "duration": info.get("duration", 0),
            "audio_path": str(existing[0]),
            "url": url,
        }

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(YOUTUBE_AUDIO_DIR / f"{video_id}.%(ext)s"),
        "quiet": False,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Найти скачанный файл
    audio_files = list(YOUTUBE_AUDIO_DIR.glob(f"{video_id}.*"))
    if not audio_files:
        raise RuntimeError(f"Аудио не найдено после скачивания: {video_id}")

    audio_path = audio_files[0]
    print(f"[YT] Скачано: {audio_path.name} ({info.get('duration', 0)//60} мин)")

    return {
        "video_id": video_id,
        "title": info.get("title", ""),
        "channel": info.get("channel", info.get("uploader", "")),
        "duration": info.get("duration", 0),
        "audio_path": str(audio_path),
        "url": url,
    }


def _decode_full_audio(audio_path: str) -> "np.ndarray":
    """Декодировать весь аудиофайл потоково через PyAV.

    Читает последовательно (без seek), совместимо с webm/opus.
    Returns:
        numpy array float32, mono, 16kHz
    """
    import av
    import numpy as np

    SAMPLE_RATE = 16000

    container = av.open(audio_path)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

    # Собираем по частям, чтобы не держать всё в памяти как list
    chunk_arrays = []
    chunk_size = 0
    FLUSH_EVERY = SAMPLE_RATE * 60  # склеиваем каждую минуту

    buf = []
    buf_samples = 0

    total_samples = 0
    for frame in container.decode(audio=0):
        for resampled in resampler.resample(frame):
            arr = resampled.to_ndarray().flatten()
            buf.append(arr)
            buf_samples += len(arr)

        if buf_samples >= FLUSH_EVERY:
            chunk_arrays.append(np.concatenate(buf))
            total_samples += buf_samples
            minutes_done = total_samples / SAMPLE_RATE / 60
            print(f"\r[Whisper] Декодирую аудио... {minutes_done:.0f} мин", end="", flush=True)
            buf = []
            buf_samples = 0

    # Остаток
    if buf:
        chunk_arrays.append(np.concatenate(buf))

    container.close()

    if not chunk_arrays:
        return np.array([], dtype=np.float32)

    audio = np.concatenate(chunk_arrays)
    return audio.astype(np.float32) / 32768.0


def transcribe(
    audio_path: str,
    video_meta: Dict,
    model_size: str = "large-v3",
    chunk_minutes: int = 5,
) -> Dict:
    """Транскрибировать аудио через faster-whisper.

    Длинные файлы обрабатываются чанками по chunk_minutes минут
    для экономии RAM.

    Returns:
        dict с ключами: video_id, title, url, channel, duration_seconds,
                        segments, full_text, model, transcribed_at
    """
    from faster_whisper import WhisperModel

    video_id = video_meta["video_id"]
    YOUTUBE_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = YOUTUBE_TRANSCRIPTS_DIR / f"{video_id}.json"

    # Проверяем кэш
    if cache_path.exists():
        print(f"[Whisper] Транскрипт уже есть: {cache_path.name}")
        return json.loads(cache_path.read_text(encoding="utf-8"))

    # Определяем устройство
    device = "cpu"
    compute_type = "int8"
    try:
        import ctranslate2
        ctranslate2.get_supported_compute_types("cuda")
        # Добавляем cuBLAS из Ollama в PATH (Windows не находит без этого)
        import os
        cublas_dir = os.path.expanduser("~/AppData/Local/Programs/Ollama/lib/ollama/cuda_v12")
        if os.path.isdir(cublas_dir):
            os.environ["PATH"] = cublas_dir + os.pathsep + os.environ.get("PATH", "")
        device = "cuda"
        compute_type = "int8_float16"  # экономит VRAM на RTX 3050 (4GB)
        print("[Whisper] Используем GPU (CUDA, int8_float16)")
    except Exception:
        print("[Whisper] Используем CPU (int8)")

    print(f"[Whisper] Загружаю модель {model_size}...")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    import numpy as np

    duration_sec = video_meta.get("duration", 0)
    duration_min = duration_sec // 60
    chunk_sec = chunk_minutes * 60
    SAMPLE_RATE = 16000

    # Декодируем весь файл последовательно (без seek — webm не дружит с seek)
    print(f"[Whisper] Декодирую аудио ({duration_min} мин)...", end="", flush=True)
    full_audio = _decode_full_audio(audio_path)
    total_sec = len(full_audio) / SAMPLE_RATE
    print(f"\r[Whisper] Декодировано: {total_sec:.0f} сек, {len(full_audio) * 4 / 1024 / 1024:.0f} МБ в RAM")

    segments = []
    full_text_parts = []
    detected_language = "ru"

    # Нарезаем на чанки по chunk_minutes мин и транскрибируем каждый
    n_chunks = max(1, int((total_sec + chunk_sec - 1) // chunk_sec))
    print(f"[Whisper] Транскрибирую {n_chunks} частей по {chunk_minutes} мин...")

    for i in range(n_chunks):
        start_sample = i * chunk_sec * SAMPLE_RATE
        end_sample = min((i + 1) * chunk_sec * SAMPLE_RATE, len(full_audio))
        chunk_audio = full_audio[start_sample:end_sample]

        start_sec = i * chunk_sec
        end_sec_actual = end_sample / SAMPLE_RATE
        print(f"  Часть {i + 1}/{n_chunks} [{format_time(start_sec)}-{format_time(end_sec_actual)}]... ", end="", flush=True)

        if len(chunk_audio) < SAMPLE_RATE:  # меньше 1 секунды
            print("пусто")
            continue

        segments_raw, info = model.transcribe(
            chunk_audio,
            language="ru",
            beam_size=5,
            vad_filter=True,
        )
        detected_language = info.language

        chunk_count = 0
        for seg in segments_raw:
            segments.append({
                "start": round(start_sec + seg.start, 2),
                "end": round(start_sec + seg.end, 2),
                "text": seg.text.strip(),
            })
            full_text_parts.append(seg.text.strip())
            chunk_count += 1

        print(f"{chunk_count} сегментов")

    # Освобождаем память
    del full_audio

    result = {
        "video_id": video_id,
        "title": video_meta.get("title", ""),
        "url": video_meta.get("url", ""),
        "channel": video_meta.get("channel", ""),
        "duration_seconds": duration_sec,
        "model": model_size,
        "language": detected_language,
        "transcribed_at": datetime.now().isoformat(),
        "segments": segments,
        "full_text": " ".join(full_text_parts),
    }

    # Кэшируем
    cache_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[Whisper] Готово: {len(segments)} сегментов, {len(result['full_text'])} символов")

    return result


def chunk_transcript(
    segments: List[Dict],
    max_chars: int = 10000,
    overlap_segments: int = 3,
) -> List[Dict]:
    """Разбить транскрипт на чанки по границам сегментов.

    Returns:
        Список: [{text, start_time, end_time, segment_count}]
    """
    if not segments:
        return []

    chunks = []
    current_text = ""
    current_start = segments[0]["start"]
    current_segments = []

    for seg in segments:
        if len(current_text) + len(seg["text"]) > max_chars and current_text:
            chunks.append({
                "text": current_text.strip(),
                "start_time": current_start,
                "end_time": current_segments[-1]["end"],
                "segment_count": len(current_segments),
            })

            # Overlap: берём последние N сегментов
            overlap = current_segments[-overlap_segments:]
            current_text = " ".join(s["text"] for s in overlap) + " "
            current_start = overlap[0]["start"]
            current_segments = list(overlap)

        current_text += seg["text"] + " "
        current_segments.append(seg)

    # Последний чанк
    if current_text.strip():
        chunks.append({
            "text": current_text.strip(),
            "start_time": current_start,
            "end_time": current_segments[-1]["end"],
            "segment_count": len(current_segments),
        })

    return chunks


def format_time(seconds: float) -> str:
    """Форматировать секунды как MM:SS."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
